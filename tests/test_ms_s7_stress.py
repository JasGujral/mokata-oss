"""MS.S7 — TWO-PROCESS STRESS TEST (M-7). The release exit gate for multi-session safety.

MS.S1–S6 each proved ONE mechanism safe under two processes, in isolation: the state store's
atomic RMW (S1), session scoping + the registry (S2), the ledger's chained append (S3), SQLite's
WAL journal mode (S4), the single-flusher mutex (S5), the undo-log / bundle / liveness / stats RMWs
(S6). Every one of those tests drives its mechanism ALONE, with nothing else contending.

That leaves the claim itself untested. "Two Claude Code windows cannot corrupt your project" is not
a statement about any one lock — it is a statement about ALL of them running AT ONCE, on ONE repo,
interleaved: the same two processes taking the state lock, the ledger lock, the flush mutex, the
undo op-lock and SQLite's own locks in whatever order the OS schedules them. Lock-ordering bugs,
self-deadlocks, and lost updates live in exactly that seam and in no single-mechanism test.

So this stage COMPOSES the load rather than re-testing the parts: two REAL processes (spawn, not
threads — a `threading.Lock` would prove nothing about two OS processes) each run a randomized,
SEEDED, mixed workload against one repo — brainstorm state writes, session saves, gated ledger
writes, journal appends racing flushes against a shared file-backed backend, SQLite reads/writes,
reversible undo-log writes, registry touches, liveness bumps — and then, at a barrier, race each
other for one bundle name.

The point is the ASSERTIONS, not the absence of a crash: a stress test that only checks "nothing
threw" passes on a corrupted repo. The workload runs ONCE (in `setUpClass`, so the budget is paid
once) and every invariant below is then asserted as its own named test, so a violation names the
mechanism that broke rather than reddening one opaque blob.

DETERMINISM. The op SEQUENCE is a pure function of the seed (`plan_ops`); the INTERLEAVING is the
OS's and is deliberately not controlled — that is the thing under test. A fixed default seed keeps
CI reproducible; `MOKATA_STRESS_SEED` re-rolls the workload for a local soak, and every failure
message carries the seed and the exact command to replay it.

BUDGET. 2 workers x `OPS_PER_WORKER` ops. Bounded by construction: no sleeps, no retries-until-pass,
no unbounded loops. See `MS_S7_BUDGET` for the wall-clock target this is tuned to.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import json
import multiprocessing as mp
import os
import random
import sqlite3
import tempfile
import time
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MANIFEST_FILENAME, MOKATA_DIR, TEMP_LOCAL_DIRNAME, teamdb
from mokata.config import STATE_DIRNAME, Surface
from mokata.govern.ledger import AuditLedger, verify_chain
from mokata.init import init_repo
from mokata.state import StateStore

_CTX = mp.get_context("spawn")   # stable across POSIX/Windows; children re-import cleanly

# --------------------------------------------------------------------------- budget + seed policy
# The default seed is FIXED: CI must run the same workload every time, so a red is reproducible and
# never a lottery. Randomness here buys op-MIX coverage, not scheduling coverage — the scheduler
# supplies that for free, differently on every run, which is why a fixed op sequence still exercises
# new interleavings each time. `MOKATA_STRESS_SEED` re-rolls the mix for a local soak.
DEFAULT_SEED = 20260713
SEED = int(os.environ.get("MOKATA_STRESS_SEED", DEFAULT_SEED))
OPS_PER_WORKER = int(os.environ.get("MOKATA_STRESS_OPS", "2000"))
WORKERS = 2
# 2 workers x 2000 ops = 4000 ops against one repo. Measured ~2.8s on a dev laptop; a CI runner is
# 2-4x slower, so the job budget is < 30s. Tuned UP from a first cut of 60 ops/worker, which
# finished in 0.16s — the two processes barely overlapped, and a storm that is over before the
# windows meet is not a stress test. At 2000 both windows are genuinely in the locks together.
MS_S7_BUDGET = "4000 ops; ~2.8s local, < 30s CI"

MEMORY_DIRNAME = "memory"        # mirrors memory.store.MEMORY_DIRNAME (import-cycle-free literal)
BUNDLE_TAG = "stress-shared"     # the ONE name both windows race for


def repro() -> str:
    """Every assertion carries this: a red in CI is replayable on a laptop, verbatim."""
    return (f"\n\n[MS.S7] seed={SEED} ops/worker={OPS_PER_WORKER} workers={WORKERS}"
            f"\n  reproduce: MOKATA_STRESS_SEED={SEED} MOKATA_STRESS_OPS={OPS_PER_WORKER}"
            f" python -m pytest tests/test_ms_s7_stress.py -q")


# --------------------------------------------------------------------------- the workload mix
# Weighted so the CONTENDED shared-file mechanisms (ledger, journal, flush, undo, registry) get the
# most traffic — those are the ones with cross-process locks and therefore the ones a lock-ordering
# bug lives in. Session-scoped state writes are cheap and frequent (that is what a real window does
# most); the gated memory write is the heaviest single op (gate -> ledger -> journal -> flush) so it
# is weighted down to keep the budget bounded.
OPS = (
    "state_write",         # MS.S1/S2 — session-scoped brainstorm RMW under the state lock
    "session_save",        # SS.S0/S2 — the save entry point (progress + checkpoint)
    "ledger_record",       # MS.S3   — chained append, cross-process seq assignment
    "gated_memory_write",  # the COMPOSITE: WriteGate -> ledger -> journal -> best-effort flush
    "journal_append",      # CM/TM   — a team write onto the shared journal
    "journal_flush",       # MS.S5   — the single-flusher mutex + idempotent apply
    "sqlite_write",        # MS.S4   — WAL write against the real memory.db
    "sqlite_read",         # MS.S4   — a concurrent reader (the DELETE-journal bug's victim)
    "undo_write",          # MS.S6   — the reversible-write op-lock (the M-6 carry-forward)
    "registry_touch",      # MS.S2   — the shared session registry RMW
    "liveness_bump",       # MS.S6   — the flush-liveness attempts counter
    "stats_read",          # MS.S6   — memory_stats, shared repo state
)
WEIGHTS = (14, 6, 12, 5, 12, 10, 10, 8, 10, 6, 4, 3)


def plan_ops(seed: int, wid: int, n: int):
    """The worker's op sequence — a PURE function of (seed, wid). This is what makes a failure
    replayable: same seed, same ops, in the same order, in both processes."""
    rng = random.Random((seed << 8) ^ wid)
    return rng.choices(OPS, weights=WEIGHTS, k=n)


# --------------------------------------------------------------------------- the shared backend
class SharedPg:
    """A file-backed stand-in for the shared `mokata_memory` table (there is no live DB in CI).

    Deliberately not an in-memory dict: exactly-once is a CROSS-PROCESS property, so the backend has
    to be genuinely shared and has to enforce CAS for real. SQLite in WAL mode does both — the same
    shape MS.S5 uses, so the stress and the unit stage agree on what "the shared table" means.
    """

    def __init__(self, path):
        self.path = path
        self._db = sqlite3.connect(path, timeout=20.0, isolation_level=None)   # autocommit
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=20000")
        self._db.execute(
            f"CREATE TABLE IF NOT EXISTS {teamdb.MEMORY_TABLE} ("
            "  id TEXT PRIMARY KEY, mtype TEXT, subject TEXT, status TEXT, doc TEXT,"
            f"  project TEXT, {teamdb.MEMORY_REVISION_COLUMN} INT NOT NULL DEFAULT 1,"
            f"  {teamdb.MEMORY_UPDATED_AT_COLUMN} TEXT)")

    def execute(self, sql, params=()):
        stmt = sql.replace("%s", "?").replace("now()", "CURRENT_TIMESTAMP")
        before = self._db.total_changes
        cur = self._db.execute(stmt, tuple(params))
        return _Cursor(cur.fetchall(), rowcount=self._db.total_changes - before)

    def ids(self):
        cur = self._db.execute(f"SELECT id FROM {teamdb.MEMORY_TABLE}")
        return [r[0] for r in cur.fetchall()]

    def close(self):
        self._db.close()


class _Cursor:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


# --------------------------------------------------------------------------- fixtures
def _repo(d, mode="team"):
    """A real repo in TEAM mode — the journal/flush path activates on team MODE, so the composite
    write (gate -> ledger -> journal -> flush) only exists here."""
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    path = os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mode"] = mode
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return Surface.load(d)


def _mem_db(surface):
    return os.path.join(surface.mokata_dir, TEMP_LOCAL_DIRNAME, MEMORY_DIRNAME, "memory.db")


def _state_dir(surface):
    return os.path.join(surface.temp_local_dir, STATE_DIRNAME)


def _payload(key, value):
    return {"id": key, "mtype": "fact", "subject": key, "status": "active",
            "doc": json.dumps({"id": key, "value": value}), "project": "p1"}


# ============================================================================ the worker (a window)
def _stress_worker(root, db, wid, seed, n_ops, go, barrier, q):
    """ONE Claude Code window: its own process, its own session identity, its own view of the repo,
    running its own seeded op sequence at full speed against the SAME repo as its sibling."""
    import _support  # noqa: F401

    # Capture any DEGRADE NOTICE this window emits (they go to stderr, which a child process throws
    # away). A degrade under load is a FINDING, not scenery — see `test_no_window_cried_degrade`.
    import mokata.memory._sqlite as _sq
    degrades = []
    _sq._stderr = degrades.append

    from mokata import flush_liveness, session_registry, session_save, team_health, team_journal
    from mokata import session as S
    from mokata.brainstorm import Approach, BrainstormSession
    from mokata.config import Surface as Sf
    from mokata.govern.ledger import AuditLedger as AL
    from mokata.govern.resume import PipelineCheckpoint
    from mokata.govern.revert import ReversibleStateStore
    from mokata.memory.backends import SQLiteBackend
    from mokata.memory.item import MemoryItem
    from mokata.memory.store import MemoryStore
    from mokata.state import StateStore as SS

    # Pin this window's session id (a real window's id is stable for its lifetime); it also lets
    # the parent assert on exact per-session filenames and registry rows.
    sid = f"stress-w{wid}"
    os.environ[S.SESSION_ID_ENV] = sid
    S.reset_for_test()

    rep = {"wid": wid, "sid": sid, "errors": [], "locked": [], "ledger_seqs": [],
           "journal_keys": [], "undo_keys": [], "sqlite_ids": [], "gated_ids": [],
           "state_writes": 0, "flushes": [], "bundle": None, "ops": 0, "degrades": degrades}

    surface = Sf.load(root)
    state = surface.state                                  # session-scoped (MS.S2)
    base_state = SS(_state_dir(surface))                   # the raw store the undo log rides
    rs = ReversibleStateStore(base_state)                  # built ONCE — exactly the M-6 bug shape
    ledger = AL.from_mokata_dir(surface.mokata_dir)
    sqlite_backend = SQLiteBackend(_mem_db(surface))
    store = MemoryStore.from_surface(surface)

    def _bstorm(i):
        s = BrainstormSession(f"stress topic w{wid}")
        s.ask("q?")
        s.answer(f"a{i}")
        s.propose_approaches([
            Approach("a1", "one", pros=["simple"], cons=["stale"]),
            Approach("a2", "two", pros=["fresh"], cons=["slow"]),
        ])
        return s.to_dict()

    ops = plan_ops(seed, wid, n_ops)
    go.wait(timeout=60)                                    # both windows enter the storm together

    for i, op in enumerate(ops):
        try:
            if op == "state_write":
                def _mut(cur, wid=wid, i=i):
                    cur = cur if isinstance(cur, dict) else {"turns": []}
                    cur["turns"] = list(cur.get("turns") or []) + [{"w": wid, "i": i}]
                    return cur
                state.update("brainstorm_progress", _mut)
                rep["state_writes"] += 1

            elif op == "session_save":
                session_save.save_session(surface, brainstorm=_bstorm(i), passed=["brainstorm"])

            elif op == "ledger_record":
                e = ledger.record("stress", worker=wid, op=i)
                rep["ledger_seqs"].append(e["seq"])

            elif op == "gated_memory_write":
                # The composite: WriteGate -> audit ledger -> team journal -> best-effort flush.
                res = store.remember_decision(f"gated-w{wid}-{i}", f"value {i}", assume_yes=True)
                if res.committed and res.item is not None:
                    rep["gated_ids"].append(res.item.id)

            elif op == "journal_append":
                key = f"jk-w{wid}-{i}"
                team_journal.record_team_write(
                    surface, op=team_journal.OP_PUT, table=teamdb.MEMORY_TABLE, key=key,
                    payload=_payload(key, i), ledger_id=ledger.count(), project="p1",
                    actor=f"w{wid}")
                rep["journal_keys"].append(key)

            elif op == "journal_flush":
                pg = SharedPg(db)
                try:
                    res = team_journal.flush(
                        surface, health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
                        connect=lambda *a, **k: pg)
                    rep["flushes"].append(
                        {"flushed": res.flushed, "conflicts": res.conflicts,
                         "skipped": res.skipped, "contended": res.contended,
                         "already_applied": res.already_applied})
                finally:
                    pg.close()

            elif op == "sqlite_write":
                item = MemoryItem.create(f"sq-w{wid}-{i}", f"value {i}")
                sqlite_backend.put(item)
                rep["sqlite_ids"].append(item.id)

            elif op == "sqlite_read":
                sqlite_backend.all()

            elif op == "undo_write":
                key = f"undo-w{wid}-{i}"
                rs.write(key, {"w": wid, "i": i})
                rep["undo_keys"].append(key)

            elif op == "registry_touch":
                session_registry.touch(surface, phase="stress")

            elif op == "liveness_bump":
                def _bump(st):
                    st.attempts += 1
                    return st
                flush_liveness.update_state(surface, _bump)

            elif op == "stats_read":
                store.all_active()

            rep["ops"] += 1
        except Exception as exc:                            # noqa: BLE001 - the report IS the point
            msg = f"op#{i} {op}: {type(exc).__name__}: {exc}"
            rep["errors"].append(msg)
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                rep["locked"].append(msg)

    # ---------------------------------------------------------------- the synchronized bundle race
    # Both windows PLAN the same bundle name (each sees "new"), then the barrier holds them until
    # both have planned — only then do they commit. Exactly one must win; the loser must get an
    # honest error, not a silent clobber of its sibling's session.
    try:
        from mokata import session_bundle as SB
        run_id = f"stress-run-{wid}"
        PipelineCheckpoint(surface.state, run_id).mark_passed("brainstorm")
        plan = SB.plan_session_push(root, surface, BUNDLE_TAG, run_id=run_id)
        barrier.wait(timeout=60)
        try:
            SB.commit_session_push(plan)
            rep["bundle"] = "won"
        except SB.SessionBundleError as exc:
            rep["bundle"] = f"lost: {exc}"
    except Exception as exc:                                # noqa: BLE001
        rep["bundle"] = f"error: {type(exc).__name__}: {exc}"

    q.put(rep)


# ============================================================================ the stress run
class TwoProcessStress(unittest.TestCase):
    """The workload runs ONCE for the whole class (the budget is paid once); each invariant below is
    a separate named test, so a violation names the mechanism that broke."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = cls._tmp.name
        cls.surface = _repo(cls.root)
        cls.db = os.path.join(cls.root, "shared_pg.sqlite3")
        SharedPg(cls.db).close()                    # create the shared table up-front

        go, barrier, q = _CTX.Event(), _CTX.Barrier(WORKERS), _CTX.Queue()
        procs = [_CTX.Process(target=_stress_worker,
                              args=(cls.root, cls.db, wid, SEED, OPS_PER_WORKER, go, barrier, q))
                 for wid in range(WORKERS)]
        for p in procs:
            p.start()
        go.set()
        cls.reports = [q.get(timeout=180) for _ in range(WORKERS)]
        for p in procs:
            p.join(timeout=60)
        cls.exitcodes = [p.exitcode for p in procs]

        # Drain whatever the storm left pending, so the exactly-once assertions see the final state.
        from mokata import team_health, team_journal
        pg = SharedPg(cls.db)
        cls.final_flush = team_journal.flush(
            cls.surface, health=team_health.HealthVerdict(team_health.HEALTHY, "reachable"),
            connect=lambda *a, **k: pg)
        cls.backend_ids = pg.ids()
        pg.close()
        cls.journal = team_journal.TeamJournal.for_surface(cls.surface)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _all(self, field):
        return [x for r in self.reports for x in r[field]]

    # ---------------------------------------------------------------- the storm itself completed
    def test_both_windows_survived_the_storm(self):
        """No child crashed, and every op it planned it also ran. A stress test whose workers died
        early proves nothing — this is the precondition for every assertion below."""
        self.assertEqual(self.exitcodes, [0] * WORKERS,
                         f"a stress worker crashed: exitcodes={self.exitcodes}{repro()}")
        for r in self.reports:
            self.assertEqual(r["ops"], OPS_PER_WORKER,
                             f"worker {r['wid']} ran {r['ops']}/{OPS_PER_WORKER} ops{repro()}")

    def test_no_operation_raised(self):
        """Every mechanism tolerated the contention. Any exception here is the finding — a lock
        timeout, a self-deadlock, a torn read — and the message names the op that broke."""
        errors = self._all("errors")
        self.assertEqual(errors, [], f"{len(errors)} op(s) raised under contention:"
                                     f"\n  " + "\n  ".join(errors) + repro())

    # ---------------------------------------------------------------- state (MS.S1 / MS.S2)
    def test_both_sessions_state_files_are_valid_and_present(self):
        """Session scoping held: each window's brainstorm state is its OWN file, both parse, and
        neither window's turns landed in the other's — the two-windows-clobbering bug, inverted."""
        base = StateStore(_state_dir(self.surface))
        for r in self.reports:
            data = base.read(f"brainstorm_progress__{r['sid']}")
            self.assertIsInstance(data, dict,
                                  f"no valid scoped state for {r['sid']}{repro()}")
            turns = data.get("turns") or []
            self.assertTrue(all(t["w"] == r["wid"] for t in turns),
                            f"{r['sid']} state holds ANOTHER window's turns{repro()}")

    def test_every_state_file_on_disk_is_valid_json(self):
        """No torn write survived the storm — every state file parses, in full."""
        sd = _state_dir(self.surface)
        for name in os.listdir(sd):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(sd, name), encoding="utf-8") as fh:
                try:
                    json.load(fh)
                except ValueError as exc:
                    self.fail(f"TORN STATE FILE {name}: {exc}{repro()}")

    # ---------------------------------------------------------------- ledger (MS.S3)
    def test_ledger_chain_verifies_intact(self):
        ledger = AuditLedger.from_mokata_dir(self.surface.mokata_dir)
        report = ledger.verify()
        self.assertTrue(report.intact,
                        f"audit ledger chain BROKE under two-process load: "
                        f"{report.render()}{repro()}")

    def test_ledger_seqs_are_unique_and_monotonic_and_nothing_was_lost(self):
        """Both windows' appends are all there, each with its own seq: no collision (two entries
        sharing a seq) and no loss (an append overwritten by the sibling's)."""
        ledger = AuditLedger.from_mokata_dir(self.surface.mokata_dir)
        seqs = [e["seq"] for e in ledger.entries()]
        self.assertEqual(seqs, sorted(set(seqs)),
                         f"ledger seqs collided or went backwards{repro()}")
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)),
                         f"ledger seqs are not gapless 1..N{repro()}")
        claimed = self._all("ledger_seqs")
        self.assertEqual(len(claimed), len(set(claimed)),
                         f"two windows were handed the SAME seq{repro()}")
        for seq in claimed:
            self.assertIn(seq, seqs, f"a seq handed to a window is missing from the "
                                     f"ledger — an append was LOST{repro()}")

    def test_the_chain_is_verifiable_as_a_pure_walk_too(self):
        """Belt and braces: the pure chain walk agrees with the store's own verify."""
        entries = AuditLedger.from_mokata_dir(self.surface.mokata_dir).entries()
        self.assertTrue(verify_chain(entries).intact, f"pure chain walk found a break{repro()}")

    # ---------------------------------------------------------------- journal + flush (MS.S5)
    def test_backend_rows_are_exactly_once(self):
        """The single-flusher invariant: every journaled write reached the shared table EXACTLY
        once. A duplicate here is a double-apply; a missing row is a dropped write."""
        expected = set(self._all("journal_keys")) | set(self._all("gated_ids"))
        ids = self.backend_ids
        self.assertEqual(len(ids), len(set(ids)),
                         f"DUPLICATE rows in the shared table — a write applied twice{repro()}")
        missing = expected - set(ids)
        self.assertEqual(missing, set(),
                         f"{len(missing)} journaled write(s) never reached the backend{repro()}")

    def test_no_phantom_conflicts(self):
        """The M-5 phantom: a window CAS-missing on a row its own sibling just wrote and calling it
        a conflict. Nothing here genuinely diverges, so every conflict is a phantom."""
        conflicts = sum(f["conflicts"] for r in self.reports for f in r["flushes"])
        conflicts += self.final_flush.conflicts
        self.assertEqual(conflicts, 0,
                         f"{conflicts} PHANTOM conflict(s) — a window conflicted with its own "
                         f"sibling's successful write{repro()}")

    def test_the_journal_fully_drained(self):
        """Nothing was stranded: after the storm and one final drain, no entry is still pending."""
        pending = self.journal.pending()
        self.assertEqual(pending, [],
                         f"{len(pending)} journal entr(ies) never flushed{repro()}")

    # ---------------------------------------------------------------- SQLite (MS.S4)
    def test_sqlite_took_no_locked_errors(self):
        """The MS.S4 claim under real load: WAL + busy_timeout means a writer never trips over a
        concurrent reader. `database is locked` here is the bug returning."""
        locked = self._all("locked")
        self.assertEqual(locked, [], f"SQLite lock contention surfaced as an error:"
                                     f"\n  " + "\n  ".join(locked) + repro())

    def test_the_store_really_ended_up_in_wal(self):
        """Skip-honesty: 'no locked errors' is worthless if WAL never turned on. Prove the mechanism
        under test was actually ENGAGED — the MS.S4 fix is what makes the rest of this class true."""
        conn = sqlite3.connect(_mem_db(self.surface))
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        finally:
            conn.close()
        self.assertEqual(mode, "wal", f"the memory store is on '{mode}', not WAL — the SQLite "
                                      f"invariants above are hollow{repro()}")

    def test_no_window_cried_degrade(self):
        """MS.S7-FIX-1 regression, at the composite level. Two windows opening one fresh repo used
        to make the WAL switch lose a race and report `database is locked` as "this filesystem
        cannot enable SQLite WAL" — a loud, FALSE, actionable lie (it told the user to move their
        store). Nothing here is degraded, so any notice is that bug."""
        notices = self._all("degrades")
        self.assertEqual(notices, [], f"a window emitted a FALSE degrade notice under contention:"
                                      f"\n  " + "\n  ".join(notices) + repro())

    def test_the_sqlite_store_is_consistent_and_holds_both_windows_writes(self):
        from mokata.memory.backends import SQLiteBackend
        backend = SQLiteBackend(_mem_db(self.surface))
        stored = {item.id for item in backend.all()}
        for wid, r in enumerate(self.reports):
            written = set(r["sqlite_ids"])
            self.assertTrue(written <= stored,
                            f"window {wid} lost {len(written - stored)} SQLite write(s){repro()}")

    # ---------------------------------------------------------------- undo log (MS.S6)
    def test_the_undo_log_holds_both_windows_entries(self):
        """The M-6 carry-forward under full load: the undo log is a read-modify-write whose gap once
        spanned the whole process lifetime. Both windows' revert points must survive."""
        base = StateStore(_state_dir(self.surface))
        data = base.read("undo_log") or {}
        # the record's field is `target` (revert.ReversibleStateStore.write) — NOT `key`
        keys = {rec.get("target") for rec in (data.get("records") or [])}
        for r in self.reports:
            missing = set(r["undo_keys"]) - keys
            self.assertEqual(missing, set(),
                             f"window {r['wid']} lost {len(missing)} undo record(s) — its revert "
                             f"points were eaten by the sibling{repro()}")

    # ---------------------------------------------------------------- registry (MS.S2)
    def test_the_registry_lists_both_sessions(self):
        """Two windows, two rows. `prune=False` because the workers are dead by now — pruning is
        MS.S2's job and is tested there; here the question is whether both ever registered."""
        from mokata import session_registry
        listed = {e.session_id for e in session_registry.list_sessions(self.surface, prune=False)}
        for r in self.reports:
            self.assertIn(r["sid"], listed,
                          f"window {r['sid']} is missing from the session registry{repro()}")

    # ---------------------------------------------------------------- the TOCTOU race (MS.S6)
    def test_exactly_one_window_wins_the_bundle_race(self):
        """Both planned the same bundle name, both saw 'new', both committed. Exactly one may win —
        and the loser must be TOLD, not silently clobbered."""
        outcomes = [r["bundle"] for r in self.reports]
        winners = [o for o in outcomes if o == "won"]
        losers = [o for o in outcomes if isinstance(o, str) and o.startswith("lost")]
        self.assertEqual(len(winners), 1,
                         f"expected exactly ONE winner of the '{BUNDLE_TAG}' race, got "
                         f"{len(winners)}: {outcomes}{repro()}")
        self.assertEqual(len(losers), WORKERS - 1,
                         f"the loser did not get an honest error: {outcomes}{repro()}")


# ============================================================================ MS.S7-FIX-1
def _wal_race_worker(path, go, q):
    """Open the memory backend the way a real window does (a connect PER operation) and hammer it,
    capturing any degrade notice this process emits."""
    import _support  # noqa: F401
    import mokata.memory._sqlite as _sq
    notices = []
    _sq._stderr = notices.append

    from mokata.memory.backends import SQLiteBackend
    from mokata.memory.item import MemoryItem

    backend = SQLiteBackend(path)
    go.wait(timeout=30)
    errors = []
    for i in range(40):
        try:
            backend.put(MemoryItem.create(f"k{os.getpid()}-{i}", "v"))
            backend.all()
        except Exception as exc:                        # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
    q.put({"notices": notices, "errors": errors})


class _BusyOnSwitch:
    """A REAL SQLite connection whose WAL SWITCH is refused with SQLITE_BUSY — the contended shape.
    `mode` is what a plain `PRAGMA journal_mode` READ reports, i.e. what the file is ACTUALLY on:
    'wal' models a sibling window having already won the switch. Everything else is real SQLite, so
    "the store still works" is proven against a real store (the MS.S4 fake idiom, extended)."""

    busy = sqlite3.OperationalError("database is locked")

    def __init__(self, real, mode="wal", exc=None):
        self._real, self._mode, self._exc = real, mode, exc or self.busy
        self.switch_attempts = 0

    def execute(self, sql, *args):
        low = sql.lower().replace(" ", "")
        if "journal_mode=" in low:                       # the mode CHANGE (needs the exclusive lock)
            self.switch_attempts += 1
            raise self._exc
        if "journal_mode" in low:                        # the plain READ (no lock needed)
            return _Cursor([(self._mode,)])
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _patched_connect(factory):
    from unittest import mock

    from mokata.memory import _sqlite as SQ
    real = sqlite3.connect
    return mock.patch.object(SQ.sqlite3, "connect",
                             side_effect=lambda p, *a, **k: factory(real(p, *a, **k)))


class WalSwitchUnderContention(unittest.TestCase):
    """MS.S7-FIX-1 — the ONE production bug this stress found (`memory/_sqlite.py`).

    Putting a db into WAL needs a brief EXCLUSIVE lock. When two windows open ONE FRESH repo at the
    same moment, both attempt that initial `delete`->`wal` switch and the loser is refused with
    SQLITE_BUSY. mokata believed the refusal and emitted its PERMANENT filesystem degrade: a loud
    notice saying the filesystem "cannot enable SQLite WAL" and advising the user to MOVE THEIR
    STORE — while the db was, in fact, in WAL, put there by the sibling a millisecond earlier.

    Two costs, both real: a user-facing lie they would act on, and (worse) the window that believed
    it stayed on the rollback journal for that connection — the very M-4 bug MS.S4 exists to kill.

    These tests are DETERMINISTIC (the end-to-end race reproduces the bug only ~3 runs in 10, far
    too weak to guard a fix), and behaviour-level: they drive `connect_sqlite`, so a green cannot be
    the notice-capture silently missing. The negative control is the load-bearing one — a filesystem
    that GENUINELY cannot do WAL must still degrade at once. A fix that swallowed every refusal
    would pass the first test and strand users on the rollback journal in silence.
    """

    def _connect(self, factory, **kw):
        from mokata.memory._sqlite import connect_sqlite
        notices, seen = [], set()
        with tempfile.TemporaryDirectory() as d, _patched_connect(factory):
            conn = connect_sqlite(os.path.join(d, "memory.db"),
                                  out=notices.append, seen=seen, **kw)
            conn.close()
        return notices

    def test_a_busy_switch_is_silent_when_a_sibling_already_won_it(self):
        """THE regression. BUSY, but the file reads back as WAL — a sibling window switched it. That
        is the fix working, not a degrade: say NOTHING. (Pre-fix: one loud, false notice.)"""
        made = []
        notices = self._connect(lambda real: made.append(
            _BusyOnSwitch(real, mode="wal")) or made[-1])
        self.assertEqual(notices, [], "a transient SQLITE_BUSY on the WAL switch was reported as a "
                                      "PERMANENT filesystem degrade:\n  " + "\n  ".join(notices))
        self.assertEqual(made[0].switch_attempts, 1, "already-WAL needs no retry loop at all")

    def test_a_busy_switch_that_never_lands_is_retried_then_reported_honestly(self):
        """BUSY that never clears and a file still on the rollback journal: wait out the bound, then
        degrade — HONESTLY (naming 'delete', the mode we are really on) and BOUNDED (the bound is
        what keeps this CI-safe). Silence here would be the M-4 bug, restored."""
        made = []
        start = time.monotonic()
        notices = self._connect(
            lambda real: made.append(_BusyOnSwitch(real, mode="delete")) or made[-1],
            busy_timeout_ms=100)          # a tiny bound so the tail case stays fast
        elapsed = time.monotonic() - start
        self.assertEqual(len(notices), 1, f"a genuinely stuck WAL switch must still be LOUD: {notices}")
        self.assertIn("DEGRADED", notices[0])
        self.assertIn("delete", notices[0])                 # names the mode it really fell back TO
        self.assertGreater(made[0].switch_attempts, 1, "a BUSY refusal must be RETRIED, not believed")
        self.assertLess(elapsed, 5.0, "the retry must be BOUNDED by the busy bound")

    def test_a_genuine_wal_refusal_still_degrades_immediately(self):
        """The negative control: a mount that truly cannot back the -shm index raises something that
        is NOT busy. Degrade at once — never retried, never swallowed. (MS.S4's contract, intact.)"""
        made = []
        notices = self._connect(lambda real: made.append(_BusyOnSwitch(
            real, mode="delete", exc=sqlite3.OperationalError("disk I/O error"))) or made[-1])
        self.assertEqual(len(notices), 1)
        self.assertIn("DEGRADED", notices[0])
        self.assertEqual(made[0].switch_attempts, 1, "a non-busy refusal must NOT be retried")

    def test_two_windows_opening_one_fresh_db_raise_no_false_degrade(self):
        """The reproducer as the stress originally hit it: two REAL processes, one fresh db,
        connect-per-op. Kept as the end-to-end witness — but note it only catches the bug ~3 runs in
        10, which is precisely why the deterministic tests above are what guard the fix."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "memory.db")
            go, q = _CTX.Event(), _CTX.Queue()
            procs = [_CTX.Process(target=_wal_race_worker, args=(path, go, q)) for _ in range(2)]
            for p in procs:
                p.start()
            go.set()
            reps = [q.get(timeout=120) for _ in procs]
            for p in procs:
                p.join(timeout=60)

            notices = [n for r in reps for n in r["notices"]]
            errors = [e for r in reps for e in r["errors"]]
            self.assertEqual(notices, [], "a FALSE 'filesystem cannot enable WAL' notice was "
                                          "emitted for what was only lock contention:\n  "
                                          + "\n  ".join(notices))
            self.assertEqual(errors, [], f"the racing windows errored: {errors[:3]}")

            conn = sqlite3.connect(path)
            try:                                        # and it really is in WAL — the lie's proof
                self.assertEqual(
                    conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            finally:
                conn.close()


# ============================================================================ determinism
class SeededDeterminism(unittest.TestCase):
    """The seed is the reproduction handle: it must actually determine the workload."""

    def test_a_fixed_seed_reproduces_the_same_op_sequence(self):
        self.assertEqual(plan_ops(SEED, 0, 40), plan_ops(SEED, 0, 40))
        self.assertEqual(plan_ops(SEED, 1, 40), plan_ops(SEED, 1, 40))

    def test_a_different_seed_rolls_a_different_workload(self):
        self.assertNotEqual(plan_ops(SEED, 0, 40), plan_ops(SEED + 1, 0, 40))

    def test_the_two_windows_run_different_sequences(self):
        """Same seed, different worker: the mixes must differ, or both windows would march in
        lock-step and the interleaving would never be adversarial."""
        self.assertNotEqual(plan_ops(SEED, 0, 40), plan_ops(SEED, 1, 40))

    def test_every_op_in_the_mix_is_dispatchable(self):
        """A typo in `OPS` would silently become a no-op in the worker's if/elif chain — the op
        would be 'run' and assert nothing. Pin the mix against the worker's dispatch."""
        import inspect
        body = inspect.getsource(_stress_worker)
        for op in OPS:
            self.assertIn(f'"{op}"', body, f"op '{op}' is in the mix but the worker never "
                                           f"dispatches it — a silent no-op")

    def test_the_failure_message_carries_the_seed(self):
        self.assertIn(f"seed={SEED}", repro())
        self.assertIn("MOKATA_STRESS_SEED", repro())


if __name__ == "__main__":
    unittest.main()
