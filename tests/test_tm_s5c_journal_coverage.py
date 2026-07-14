"""TM.S5c — journal-coverage completion (SHIP-BLOCKER, doc 48 C1/C5 + doc 63).

S5b routed only `remember()` through the journal-first/CAS path; EVERY other durable memory write
(`promote`, `promote_scope`, `propose`, the `_transition` approve/publish/reject steps, `rollback`,
self-healing `apply_proposal`, and `apply_consolidation` incl. the PRUNE delete) went DIRECT to
`self.backend` with a non-CAS upsert/delete → silent last-writer-wins in team mode, and a hard
delete of a shared row on PRUNE.

These tests pin the fix, using the audit's repro pattern — a recording backend + a store forced
into team mode:
  * in TEAM mode EVERY method JOURNALS the durable write and NEVER touches `self.backend`;
  * each journal entry carries a `base_revision` (the CAS base) + the right op (put/update/delete);
  * a STALE `base_revision` SURFACES a CONFLICT at flush (no silent last-writer-wins);
  * PRUNE journals a DELETE op, never a direct `backend.delete` of a shared row;
  * LOCAL mode is byte-identical — the backend path, no journal — for every method;
  * publish refuses when the publisher IS the proposer (separation of duties at publish).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, team_journal
from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore
from mokata.memory.backends import MemoryBackend
from mokata.memory.consolidation import MERGE, PRUNE, ConsolidationProposal
from mokata.memory.healing import CONTRADICTION, STALE, HealingProposal
from mokata.memory.item import ACTIVE, PROPOSED, RULE, SUPERSEDED
from mokata.memory import review as R


# --------------------------------------------------------------------------- fixtures
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


REV = 7   # the revision the recording backend reports for every stored item (the CAS base)


class RecordingBackend(MemoryBackend):
    """The audit's repro tool: a backend that RECORDS every put/update/delete so a test can assert
    a method NEVER wrote to it in team mode. Reads attach `_revision` (like PostgresBackend) so the
    store can capture the CAS base."""

    name = "recording"

    def __init__(self, items=()):
        self.store = {it.id: it for it in items}
        self.calls = []            # [(op, id), ...] — every durable write that reached the backend

    def _tag(self, item):
        if item is not None:
            item._revision = REV
        return item

    def put(self, item):
        self.calls.append(("put", item.id))
        self.store[item.id] = item

    def update(self, item):
        self.calls.append(("update", item.id))
        self.store[item.id] = item

    def delete(self, item_id):
        self.calls.append(("delete", item_id))
        return self.store.pop(item_id, None) is not None

    def get(self, item_id):
        return self._tag(self.store.get(item_id))

    def all(self, mtype=None, statuses=None):
        out = []
        for it in self.store.values():
            self._tag(it)
            if mtype is not None and it.mtype != mtype:
                continue
            if statuses is not None and it.status not in statuses:
                continue
            out.append(it)
        return out

    def close(self):
        pass


class _EnvClean(unittest.TestCase):
    """Hermetic: a stray $MOKATA_PG_DSN must not let the best-effort auto-flush reach a real DB —
    these exercise the OFFLINE/journal path deterministically (like the S5b suite)."""

    def setUp(self):
        self._saved = os.environ.pop("MOKATA_PG_DSN", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["MOKATA_PG_DSN"] = self._saved


def _team_store(d, backend):
    """A store FORCED into team mode over the recording backend, with the S10 access policy +
    S6 scope context turned OFF so these tests isolate the JOURNAL path (access/scope are pinned
    by their own suites). `_team_mode()` reads the surface mode, not `access`, so it stays True."""
    surface = _repo(d, mode="team")
    store = MemoryStore.from_surface(surface)
    store.backend = backend
    store.access = None
    store.scope_context = None
    return surface, store


def _local_store(d, backend):
    surface = _repo(d, mode=None)
    store = MemoryStore.from_surface(surface)
    store.backend = backend
    return surface, store


def _pending(surface):
    return team_journal.TeamJournal.for_surface(surface).pending()


def _rule(subject="style.tabs", value="use tabs", **kw):
    return MemoryItem.create(subject, value, kind=RULE, **kw)


def _proposal(state, proposer="alice", approver="", base_id="", status=PROPOSED, **kw):
    it = MemoryItem.create("api.retries", "3", **kw)
    it.status = status
    it.review = {"state": state, "proposer": proposer, "approver": approver,
                 "base_id": base_id, "change": "edit"}
    return it


# ======================================================= per-method: TEAM journals, no backend
class TestPromoteJournalsInTeamMode(_EnvClean):
    def test_promote_journals_an_update_and_never_touches_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            item = _rule()
            backend = RecordingBackend([item])
            surface, store = _team_store(d, backend)
            res = store.promote(item.id, "soft", confirm=lambda _t: True)
            self.assertTrue(res.changed)
            self.assertEqual(backend.calls, [], "team promote must NOT write to the backend")
            pend = _pending(surface)
            self.assertEqual([e.op for e in pend], ["memory_update"])
            self.assertEqual(pend[0].key, item.id)
            self.assertEqual(pend[0].base_revision, REV, "CAS base = the revision it was read at")


class TestPromoteScopeJournalsInTeamMode(_EnvClean):
    def test_promote_scope_journals_an_update_and_never_touches_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            item = MemoryItem.create("db.dsn", "x")          # personal (default) → broaden to project
            backend = RecordingBackend([item])
            surface, store = _team_store(d, backend)
            res = store.promote_scope(item.id, "project", confirm=lambda _t: True)
            self.assertTrue(res.changed)
            self.assertEqual(backend.calls, [])
            pend = _pending(surface)
            self.assertEqual([e.op for e in pend], ["memory_update"])
            self.assertEqual(pend[0].base_revision, REV)


class TestProposeJournalsInTeamMode(_EnvClean):
    def test_propose_journals_a_put_for_the_new_draft_and_never_touches_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            backend = RecordingBackend()
            surface, store = _team_store(d, backend)
            item = MemoryItem.create("cache.ttl", "60")
            res = store.propose(item, change="new", confirm=lambda _t: True)
            self.assertTrue(res.ok)
            self.assertEqual(backend.calls, [])
            pend = _pending(surface)
            self.assertEqual([e.op for e in pend], ["memory_put"])
            self.assertIsNone(pend[0].base_revision, "a new draft is believed-new (INSERT-or-conflict)")


class TestApproveJournalsInTeamMode(_EnvClean):
    def test_approve_journals_an_update_and_never_touches_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            prop = _proposal(R.IN_REVIEW, proposer="alice")
            backend = RecordingBackend([prop])
            surface, store = _team_store(d, backend)
            res = store.approve(prop.id, actor="bob", confirm=lambda _t: True)
            self.assertTrue(res.ok, res.message)
            self.assertEqual(res.state, R.APPROVED)
            self.assertEqual(backend.calls, [])
            self.assertEqual([e.op for e in _pending(surface)], ["memory_update"])
            self.assertEqual(_pending(surface)[0].base_revision, REV)


class TestPublishJournalsInTeamMode(_EnvClean):
    def test_publish_journals_an_update_and_never_touches_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            prop = _proposal(R.APPROVED, proposer="alice", approver="bob")
            backend = RecordingBackend([prop])
            surface, store = _team_store(d, backend)
            res = store.publish(prop.id, actor="bob", confirm=lambda _t: True)
            self.assertTrue(res.ok, res.message)
            self.assertEqual(res.state, R.PUBLISHED)
            self.assertEqual(backend.calls, [])
            self.assertEqual([e.op for e in _pending(surface)], ["memory_update"])


class TestPublishRefusesSelfPublish(_EnvClean):
    def test_publish_refuses_when_the_publisher_is_the_proposer(self):
        with tempfile.TemporaryDirectory() as d:
            prop = _proposal(R.APPROVED, proposer="alice", approver="bob")
            backend = RecordingBackend([prop])
            surface, store = _team_store(d, backend)
            res = store.publish(prop.id, actor="alice", confirm=lambda _t: True)
            self.assertFalse(res.ok)
            self.assertTrue(res.aborted)
            self.assertIn("separation of duties", res.message)
            self.assertEqual(backend.calls, [])
            self.assertEqual(_pending(surface), [], "a refused publish journals nothing")


class TestRejectJournalsInTeamMode(_EnvClean):
    def test_reject_journals_an_update_and_never_touches_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            prop = _proposal(R.IN_REVIEW, proposer="alice")
            backend = RecordingBackend([prop])
            surface, store = _team_store(d, backend)
            res = store.reject(prop.id, actor="bob", confirm=lambda _t: True)
            self.assertTrue(res.ok, res.message)
            self.assertEqual(res.state, R.REJECTED)
            self.assertEqual(backend.calls, [])
            self.assertEqual([e.op for e in _pending(surface)], ["memory_update"])


class TestRollbackJournalsInTeamMode(_EnvClean):
    def test_rollback_journals_two_updates_and_never_touches_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            prior = MemoryItem.create("api.retries", "2")
            prior.status = SUPERSEDED
            current = MemoryItem.create("api.retries", "3")
            current.supersedes = [prior.id]
            backend = RecordingBackend([prior, current])
            surface, store = _team_store(d, backend)
            res = store.rollback(current.id, actor="bob", confirm=lambda _t: True)
            self.assertTrue(res.ok, res.message)
            self.assertEqual(backend.calls, [])
            pend = _pending(surface)
            self.assertEqual([e.op for e in pend], ["memory_update", "memory_update"])
            self.assertEqual({e.base_revision for e in pend}, {REV})


class TestSelfHealingApplyJournalsInTeamMode(_EnvClean):
    def test_stale_apply_journals_an_update_and_never_touches_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            item = MemoryItem.create("token.ttl", "5m")
            backend = RecordingBackend([item])
            surface, store = _team_store(d, backend)
            p = HealingProposal(kind=STALE, subject=item.subject, mtype=item.mtype,
                                old=backend.get(item.id), new=None, rationale="expired")
            res = store.apply_proposal(p, "approve", confirm=lambda _t: True)
            self.assertTrue(res.changed)
            self.assertEqual(backend.calls, [])
            self.assertEqual([e.op for e in _pending(surface)], ["memory_update"])
            self.assertEqual(_pending(surface)[0].base_revision, REV)

    def test_contradiction_apply_journals_two_updates_and_never_touches_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            old = MemoryItem.create("db.engine", "mysql")
            new = MemoryItem.create("db.engine", "postgres")
            backend = RecordingBackend([old, new])
            surface, store = _team_store(d, backend)
            p = HealingProposal(kind=CONTRADICTION, subject="db.engine", mtype=old.mtype,
                                old=backend.get(old.id), new=backend.get(new.id),
                                rationale="disagree")
            res = store.apply_proposal(p, "approve", confirm=lambda _t: True)
            self.assertTrue(res.changed)
            self.assertEqual(backend.calls, [])
            self.assertEqual([e.op for e in _pending(surface)],
                             ["memory_update", "memory_update"])


class TestConsolidationPruneJournalsDelete(_EnvClean):
    def test_prune_journals_a_delete_op_and_never_hard_deletes_a_shared_row(self):
        with tempfile.TemporaryDirectory() as d:
            stale = MemoryItem.create("old.note", "gone")
            backend = RecordingBackend([stale])
            surface, store = _team_store(d, backend)
            p = ConsolidationProposal(kind=PRUNE, mtype="*", subject="(stale)",
                                      olds=[backend.get(stale.id)], new=None)
            res = store.apply_consolidation(p, "approve", assume_yes=True)
            self.assertTrue(res.changed)
            self.assertEqual(backend.calls, [],
                             "team PRUNE must NOT backend.delete a shared row")
            pend = _pending(surface)
            self.assertEqual([e.op for e in pend], ["memory_delete"])
            self.assertEqual(pend[0].key, stale.id)
            self.assertEqual(pend[0].base_revision, REV, "the delete is CAS-guarded")

    def test_merge_journals_updates_and_never_touches_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            a = MemoryItem.create("x", "1")
            b = MemoryItem.create("x", "1")
            backend = RecordingBackend([a, b])
            surface, store = _team_store(d, backend)
            olds = backend.all()
            p = ConsolidationProposal(kind=MERGE, mtype=a.mtype, subject="x",
                                      olds=olds, new=olds[-1])
            res = store.apply_consolidation(p, "approve", assume_yes=True)
            self.assertTrue(res.changed)
            self.assertEqual(backend.calls, [])
            self.assertTrue(all(e.op in ("memory_update", "memory_put")
                                for e in _pending(surface)))


# ============================================================ CAS: a stale base surfaces a conflict
class _Cursor:
    def __init__(self, rows=(), rowcount=1):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeMemPg:
    """A tiny in-memory `mokata_memory` with revision CAS for INSERT / UPDATE / DELETE (no live PG)."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})

    def execute(self, sql, params=()):
        low = " ".join(sql.lower().split())
        if low.startswith("insert into mokata_memory"):
            rid = params[0]
            if rid in self.rows:
                return _Cursor(rowcount=0)
            self.rows[rid] = {"doc": params[4], "revision": 1}
            return _Cursor(rowcount=1)
        if low.startswith("update mokata_memory"):
            rid, base = params[-2], params[-1]
            row = self.rows.get(rid)
            if row and row["revision"] == base:
                row["revision"] += 1
                row["doc"] = params[3]
                return _Cursor(rowcount=1)
            return _Cursor(rowcount=0)
        if low.startswith("delete from mokata_memory"):
            rid, base = params[0], params[1]
            row = self.rows.get(rid)
            if row and row["revision"] == base:
                del self.rows[rid]
                return _Cursor(rowcount=1)
            return _Cursor(rowcount=0)
        if low.startswith("select") and "from mokata_memory" in low:
            row = self.rows.get(params[0])
            return _Cursor([(row["doc"], row["revision"])]) if row else _Cursor([])
        return _Cursor()


def _seed(surface, op, key, base_revision):
    item = MemoryItem.create("x", "v", id=key)
    payload = {"id": key, "mtype": item.mtype, "subject": item.subject,
               "status": item.status, "doc": json.dumps(item.to_dict()), "project": None}
    return team_journal.record_team_write(
        surface, op=op, table="mokata_memory", key=key, payload=payload,
        ledger_id=1, base_revision=base_revision)


def _flush(surface, pg):
    from mokata import team_health
    healthy = team_health.HealthVerdict(team_health.HEALTHY, "reachable")
    return team_journal.flush(surface, health=healthy, connect=lambda *a, **k: pg)


class TestStaleUpdateSurfacesConflict(_EnvClean):
    def test_a_stale_update_base_revision_flushes_to_a_conflict_not_a_silent_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            pg = _FakeMemPg({"k1": {"doc": "{}", "revision": 2}})   # remote already advanced
            _seed(surface, "memory_update", "k1", base_revision=1)  # we based on the OLD revision
            res = _flush(surface, pg)
            self.assertEqual(res.flushed, 0)
            self.assertEqual(res.conflicts, 1, "a lost update SURFACES — never last-writer-wins")
            self.assertEqual(pg.rows["k1"]["revision"], 2, "the remote row is untouched")

    def test_a_matching_update_base_revision_flushes_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            pg = _FakeMemPg({"k1": {"doc": "{}", "revision": 2}})
            _seed(surface, "memory_update", "k1", base_revision=2)  # based on the CURRENT revision
            res = _flush(surface, pg)
            self.assertEqual(res.flushed, 1)
            self.assertEqual(res.conflicts, 0)
            self.assertEqual(pg.rows["k1"]["revision"], 3)


class TestStaleDeleteSurfacesConflict(_EnvClean):
    def test_a_stale_delete_base_revision_flushes_to_a_conflict_not_a_hard_delete(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            pg = _FakeMemPg({"k1": {"doc": "{}", "revision": 2}})
            _seed(surface, "memory_delete", "k1", base_revision=1)  # stale base
            res = _flush(surface, pg)
            self.assertEqual(res.conflicts, 1)
            self.assertIn("k1", pg.rows, "a concurrent change blocks the delete — the row survives")

    def test_a_matching_delete_base_revision_removes_the_row(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            pg = _FakeMemPg({"k1": {"doc": "{}", "revision": 2}})
            _seed(surface, "memory_delete", "k1", base_revision=2)
            res = _flush(surface, pg)
            self.assertEqual(res.flushed, 1)
            self.assertNotIn("k1", pg.rows)


# ============================================================ LOCAL mode is byte-identical
class TestLocalModeUnchanged(_EnvClean):
    """Every method's else-branch is exactly today's backend path — the backend is written, and no
    team journal file is ever created."""

    def _assert_no_journal(self, surface):
        self.assertFalse(os.path.exists(team_journal.TeamJournal.for_surface(surface).path),
                         "local mode must never create the team journal file")

    def test_promote_local_writes_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            item = _rule()
            backend = RecordingBackend([item])
            surface, store = _local_store(d, backend)
            store.promote(item.id, "soft", confirm=lambda _t: True)
            self.assertEqual(backend.calls, [("update", item.id)])
            self._assert_no_journal(surface)

    def test_promote_scope_local_writes_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            item = MemoryItem.create("db.dsn", "x")
            backend = RecordingBackend([item])
            surface, store = _local_store(d, backend)
            store.promote_scope(item.id, "project", confirm=lambda _t: True)
            self.assertEqual(backend.calls, [("update", item.id)])
            self._assert_no_journal(surface)

    def test_propose_local_writes_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            backend = RecordingBackend()
            surface, store = _local_store(d, backend)
            item = MemoryItem.create("cache.ttl", "60")
            store.propose(item, change="new", confirm=lambda _t: True)
            self.assertEqual(backend.calls, [("put", item.id)])
            self._assert_no_journal(surface)

    def test_approve_local_writes_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            prop = _proposal(R.IN_REVIEW, proposer="alice")
            backend = RecordingBackend([prop])
            surface, store = _local_store(d, backend)
            store.approve(prop.id, actor="bob", confirm=lambda _t: True)
            self.assertEqual(backend.calls, [("update", prop.id)])
            self._assert_no_journal(surface)

    def test_publish_local_writes_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            prop = _proposal(R.APPROVED, proposer="alice", approver="bob")
            backend = RecordingBackend([prop])
            surface, store = _local_store(d, backend)
            store.publish(prop.id, actor="bob", confirm=lambda _t: True)
            self.assertEqual(backend.calls, [("update", prop.id)])
            self._assert_no_journal(surface)

    def test_reject_local_writes_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            prop = _proposal(R.IN_REVIEW, proposer="alice")
            backend = RecordingBackend([prop])
            surface, store = _local_store(d, backend)
            store.reject(prop.id, actor="bob", confirm=lambda _t: True)
            self.assertEqual(backend.calls, [("update", prop.id)])
            self._assert_no_journal(surface)

    def test_rollback_local_writes_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            prior = MemoryItem.create("api.retries", "2")
            prior.status = SUPERSEDED
            current = MemoryItem.create("api.retries", "3")
            current.supersedes = [prior.id]
            backend = RecordingBackend([prior, current])
            surface, store = _local_store(d, backend)
            store.rollback(current.id, actor="bob", confirm=lambda _t: True)
            self.assertEqual(backend.calls, [("update", current.id), ("update", prior.id)])
            self._assert_no_journal(surface)

    def test_self_healing_apply_local_writes_the_backend(self):
        with tempfile.TemporaryDirectory() as d:
            item = MemoryItem.create("token.ttl", "5m")
            backend = RecordingBackend([item])
            surface, store = _local_store(d, backend)
            p = HealingProposal(kind=STALE, subject=item.subject, mtype=item.mtype,
                                old=backend.get(item.id), new=None, rationale="expired")
            store.apply_proposal(p, "approve", confirm=lambda _t: True)
            self.assertEqual(backend.calls, [("update", item.id)])
            self._assert_no_journal(surface)

    def test_consolidation_prune_local_deletes_the_backend_row(self):
        with tempfile.TemporaryDirectory() as d:
            stale = MemoryItem.create("old.note", "gone")
            backend = RecordingBackend([stale])
            surface, store = _local_store(d, backend)
            p = ConsolidationProposal(kind=PRUNE, mtype="*", subject="(stale)",
                                      olds=[backend.get(stale.id)], new=None)
            store.apply_consolidation(p, "approve", assume_yes=True)
            self.assertEqual(backend.calls, [("delete", stale.id)],
                             "local PRUNE still hard-deletes (single-user, no sharing)")
            self._assert_no_journal(surface)


# ============================================ D5: a DB failure mid-flush NEVER fakes a success
class _BrokenSelectPg(_FakeMemPg):
    """A `mokata_memory` whose SELECT fails — a transient DB error on exactly the statement
    `_read_remote` runs. Writes still work, so this isolates the CAS-miss RE-READ."""

    def execute(self, sql, params=()):
        if " ".join(sql.lower().split()).startswith("select"):
            raise RuntimeError("connection reset by peer")
        return super().execute(sql, params)


class _DownPg(_FakeMemPg):
    """A connection that dies on EVERY statement — health said OK, then the DB went away mid-flush.
    Before D5 this propagated out of `apply_memory_write` and CRASHED the whole flush/sync."""

    def execute(self, sql, params=()):
        raise RuntimeError("server closed the connection unexpectedly")


class _LedgerSpy:
    def __init__(self):
        self.rows = []

    def record(self, kind, **fields):
        self.rows.append((kind, fields))


def _flush_l(surface, pg, ledger=None):
    from mokata import team_health
    healthy = team_health.HealthVerdict(team_health.HEALTHY, "reachable")
    return team_journal.flush(surface, health=healthy, connect=lambda *a, **k: pg, ledger=ledger)


class TestADbFailureMidFlushLeavesTheEntryPending(_EnvClean):
    """D5 — THE DATA-LOSS ONE. `_read_remote` swallowed every DB error into `None`, and `None`
    means "no such row remotely" — which on the DELETE path is the SUCCESS signal. So a transient
    error marked the user's gated PRUNE as FLUSHED and wrote a `team_flush` ledger row for a delete
    that NEVER TOUCHED Postgres: the journal, `doctor` and the audit trail all agreed a prune had
    happened that had not. A false success is not a fallback — there is nothing to fall back TO.

    The entry must now stay PENDING (nothing is lost; the next healthy flush re-applies it)."""

    def setUp(self):
        super().setUp()
        from mokata import degrade
        degrade.reset_degrade_notices()

    def test_a_delete_with_no_base_revision_is_not_flushed_when_the_remote_read_fails(self):
        # base_revision=None → `_read_remote` runs FIRST and its answer decides everything: None
        # used to mean "not remote, nothing to lose" → ApplyOutcome("ok"). The row IS remote.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            pg = _BrokenSelectPg({"k1": {"doc": "{}", "revision": 2}})
            _seed(surface, "memory_delete", "k1", base_revision=None)
            ledger = _LedgerSpy()
            res = _flush_l(surface, pg, ledger)

            self.assertEqual(res.flushed, 0, "a delete that never reached Postgres is NOT flushed")
            self.assertEqual(res.conflicts, 0, "and NOT a conflict — no concurrent writer exists")
            self.assertEqual(res.pending, 1, "it stays PENDING — nothing is lost")
            self.assertEqual([e.key for e in _pending(surface)], ["k1"])
            self.assertEqual(ledger.rows, [],
                             "NO `team_flush` ledger row for a delete that never landed")
            self.assertIn("k1", pg.rows, "and the shared row is still there — no prune happened")

    def test_a_cas_miss_reread_failure_on_delete_is_not_marked_already_applied(self):
        # A stale base → the DELETE misses the CAS → the `_read_remote` re-read decides. None used
        # to mean "the row is already gone → already_applied" → FLUSHED. It is NOT gone.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            pg = _BrokenSelectPg({"k1": {"doc": "{}", "revision": 2}})
            _seed(surface, "memory_delete", "k1", base_revision=1)     # stale base
            ledger = _LedgerSpy()
            res = _flush_l(surface, pg, ledger)

            self.assertEqual(res.flushed, 0)
            self.assertEqual(res.already_applied, 0, "an UNREAD row is not an 'already applied' one")
            self.assertEqual(res.pending, 1)
            self.assertEqual(ledger.rows, [])
            self.assertIn("k1", pg.rows)

    def test_a_dead_connection_mid_flush_does_not_crash_the_flush(self):
        """A statement that dies mid-apply used to propagate straight out of `apply_memory_write`
        and take the whole flush (and `mokata sync`) down with it. It is now a per-entry failure."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            _seed(surface, "memory_put", "k1", base_revision=None)
            _seed(surface, "memory_put", "k2", base_revision=None)
            res = _flush_l(surface, _DownPg())                          # must not raise
            self.assertEqual(res.flushed, 0)
            self.assertEqual(res.pending, 2, "EVERY entry stays pending — none is faked either way")
            self.assertEqual(sorted(e.key for e in _pending(surface)), ["k1", "k2"])

    def test_the_pending_entry_still_flushes_once_the_database_recovers(self):
        """Nothing is lost: the entry that survived the outage lands on the next healthy flush."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            healthy = _FakeMemPg({"k1": {"doc": "{}", "revision": 2}})
            _seed(surface, "memory_delete", "k1", base_revision=2)      # a VALID base
            self.assertEqual(_flush_l(surface, _DownPg()).pending, 1)   # ...but the DB is down
            self.assertIn("k1", healthy.rows, "the broken flush changed nothing")

            res = _flush_l(surface, healthy)                            # the DB comes back
            self.assertEqual(res.flushed, 1)
            self.assertEqual(res.pending, 0)
            self.assertNotIn("k1", healthy.rows, "the prune the user approved finally happens")

    def test_the_degrade_is_LOUD(self):
        """It stops being a secret: one classed `team-flush` notice, recorded for `doctor`."""
        from mokata import degrade
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            _seed(surface, "memory_delete", "k1", base_revision=None)
            _flush_l(surface, _BrokenSelectPg({"k1": {"doc": "{}", "revision": 2}}))

            notices = [n for n in degrade.emitted_notices() if n.subsystem == "team-flush"]
            self.assertEqual(len(notices), 1, "exactly one notice per subsystem per process")
            self.assertEqual(notices[0].failure_class, degrade.FAILURE_UNREACHABLE)
            self.assertIn("stays PENDING", notices[0].render())
            self.assertIn("mokata sync", notices[0].render())

    def test_an_insert_cas_miss_reread_failure_is_not_reported_as_a_phantom_conflict(self):
        """The same swallow on the PUT path invented a CONFLICT out of a read failure. A conflict
        claims a concurrent writer changed the row; a failed read knows no such thing."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, mode="team")
            pg = _BrokenSelectPg({"k1": {"doc": "{}", "revision": 1}})  # ON CONFLICT DO NOTHING
            _seed(surface, "memory_put", "k1", base_revision=None)
            res = _flush_l(surface, pg)

            self.assertEqual(res.conflicts, 0, "a read failure is not a concurrent writer")
            self.assertEqual(res.flushed, 0)
            self.assertEqual(res.pending, 1, "it stays PENDING and retries — never a fake conflict")


if __name__ == "__main__":
    unittest.main()
