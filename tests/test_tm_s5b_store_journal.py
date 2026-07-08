"""TM.S5b — MemoryStore→journal rewire (doc 48 E3 + C5, doc 63).

Makes the TM.S5 journal/health/sync subsystem LIVE on the real memory write path. In TEAM mode
the ONE WriteGate commit path in `store.py remember` routes the durable write through
`team_journal.record_team_write` (journal-first, crash-safe) AFTER the human gate approves and
the secret-scan passes — capturing the gate's approval LEDGER ID so the deferred flush inherits
the original human decision (never a bypass, P2/C5). LOCAL mode is untouched: no journal is
written, the gated commit goes straight to the backend exactly as before (zero-config default).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, team_journal
from mokata.config import Surface
from mokata.govern.ledger import AuditLedger
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore


def _repo(d, mode="team"):
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    if mode is not None:
        path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {})["mode"] = mode
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return Surface.load(d)


def _approved(surface, subject):
    """The write_gate approval ledger entries for a given memory subject."""
    ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
    return [e for e in ledger.entries()
            if e.get("kind") == "write_gate" and e.get("decision") == "approved"
            and e.get("target") == f"memory:{subject}"]


class _Cursor:
    def __init__(self, rows=(), rowcount=1):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeMemPg:
    """A tiny in-memory `mokata_memory` with revision CAS semantics (no live Postgres in CI)."""

    def __init__(self):
        self.rows = {}

    def execute(self, sql, params=()):
        low = " ".join(sql.lower().split())
        if low.startswith("insert into mokata_memory"):
            rid = params[0]
            if rid in self.rows:
                return _Cursor(rowcount=0)
            self.rows[rid] = {"id": rid, "mtype": params[1], "subject": params[2],
                              "status": params[3], "doc": params[4], "project": params[5],
                              "revision": 1}
            return _Cursor(rowcount=1)
        if low.startswith("select") and "from mokata_memory" in low:
            row = self.rows.get(params[0])
            return _Cursor([(row["doc"], row["revision"])]) if row else _Cursor([])
        return _Cursor()


class _EnvClean(unittest.TestCase):
    """Hermetic: a stray $MOKATA_PG_DSN on the dev machine must not make the store's best-effort
    auto-flush reach a real DB — these tests exercise the OFFLINE/journal path deterministically."""

    def setUp(self):
        self._saved = os.environ.pop("MOKATA_PG_DSN", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["MOKATA_PG_DSN"] = self._saved


class TestTeamRememberJournalsFirst(_EnvClean):
    def test_team_remember_journals_and_carries_the_gate_approval_id(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)                       # team mode, no DSN → offline
            store = MemoryStore.from_surface(surface)
            item = MemoryItem.create("db.engine", "postgres")
            res = store.remember(item, confirm=lambda _t: True)
            self.assertTrue(res.committed)

            # journal-first: the durable write lands in the crash-safe journal (not the backend).
            pend = team_journal.TeamJournal.for_surface(surface).pending()
            self.assertEqual(len(pend), 1)
            self.assertEqual(pend[0].key, item.id)
            self.assertEqual(pend[0].payload["subject"], "db.engine")
            self.assertEqual(store.all_active(), [],
                             "team mode defers durability to the flush — no direct backend write")

            # the captured approval ledger id IS the gate's approval seq (C5: deferred durability,
            # never deferred consent — the journal entry links back to the human decision).
            approved = _approved(surface, "db.engine")
            self.assertEqual(len(approved), 1)
            self.assertEqual(pend[0].ledger_id, approved[0]["seq"])

    def test_a_declined_gate_journals_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            store = MemoryStore.from_surface(surface)
            res = store.remember(MemoryItem.create("db.engine", "postgres"),
                                 confirm=lambda _t: False)   # human declines
            self.assertFalse(res.committed)
            self.assertEqual(team_journal.TeamJournal.for_surface(surface).pending(), [],
                             "a write refused at the gate is never journaled (gate-first)")


class TestOfflineTeamRememberNeverLost(_EnvClean):
    def test_offline_remember_journals_and_does_not_block_or_lose(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)                       # team mode, offline (no DSN)
            store = MemoryStore.from_surface(surface)
            res = store.remember(MemoryItem.create("api.timeout", "30s"),
                                 confirm=lambda _t: True)
            self.assertTrue(res.committed)           # returns immediately — offline never blocks
            # a brand-new journal object (a restart) still sees the write → not lost.
            pend = team_journal.TeamJournal.for_surface(surface).pending()
            self.assertEqual(len(pend), 1)
            self.assertEqual(pend[0].payload["subject"], "api.timeout")


class TestJournaledApprovalIdSurvivesToFlush(_EnvClean):
    def test_the_flushed_write_carries_the_captured_approval_id(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            store = MemoryStore.from_surface(surface)
            store.remember(MemoryItem.create("cache.ttl", "60"), confirm=lambda _t: True)
            approval_seq = _approved(surface, "cache.ttl")[0]["seq"]

            # now flush the journaled write to a (fake) healthy Postgres and confirm the flush
            # re-records THAT approval id — deferred durability inherits the original consent.
            from mokata import team_health
            pg = _FakeMemPg()
            ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
            healthy = team_health.HealthVerdict(team_health.HEALTHY, "reachable")
            res = team_journal.flush(surface, health=healthy,
                                     connect=lambda *a, **k: pg, ledger=ledger)
            self.assertEqual(res.flushed, 1)
            flush_entries = [e for e in ledger.entries() if e.get("kind") == "team_flush"]
            self.assertEqual(len(flush_entries), 1)
            self.assertEqual(flush_entries[0]["approval_ledger_id"], approval_seq)


class TestLocalRememberUnchanged(_EnvClean):
    def test_local_remember_never_journals_and_writes_straight_to_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode=None)            # local (default, zero-config)
            store = MemoryStore.from_surface(surface)
            res = store.remember(MemoryItem.create("db.engine", "postgres"),
                                 confirm=lambda _t: True)
            self.assertTrue(res.committed)
            # local path is byte-for-byte the old behaviour: backend holds it, NO journal at all.
            self.assertEqual([i.value for i in store.all_active()], ["postgres"])
            journal_path = team_journal.TeamJournal.for_surface(surface).path
            self.assertFalse(os.path.exists(journal_path),
                             "local mode must not create the team journal file")


if __name__ == "__main__":
    unittest.main()
