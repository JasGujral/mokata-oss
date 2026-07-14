"""MS.S6 — RMW / TOCTOU FILE-WRITE SAFETY (M-6, + the MS.S1 carry-forward).

MS.S1 made the STATE STORE safe for two Claude Code windows: atomic replace, and an `oslock` held
across the whole read-modify-write. Every OTHER shared file kept its plain `open(path, "w")`, so the
same two windows still clobbered each other everywhere else. The losses were silent, and they were
exactly the artifacts that exist to protect you:

  * the UNDO LOG — read into memory at construction and blind-overwritten on every write, a
    read-modify-write whose gap spans the whole process lifetime. Two windows ate each other's
    revert points (P17: a revert must never corrupt what it exists to protect). This is the MS.S1
    carry-forward, and `test_m6_regression_...` is its regression test.
  * the VAULT INDEX and SESSION BUNDLES — check-then-write. `plan_*` reads, the HUMAN GATE runs
    (an arbitrarily long window), then `commit_*` writes. A sibling window claiming the name in
    between was silently overwritten — the "never a silent clobber" promise, defeated by moving the
    clobber into the gap the gate itself opens.
  * the FLUSH LIVENESS state — load → mutate → `json.dump`. A lost `attempts` increment means the
    RETRY CAP (the only thing bounding retries against a dead DB) arrives late or never.
  * `memory_stats` — explicitly shared repo state, counted in memory and blind-written, so each
    window counted only its own reads and the surfaced ratio silently under-reported.

The tests use REAL PROCESSES (spawn), not threads: a `threading.Lock` is per-process and would prove
nothing about the bug, which is two OS processes. Each race is forced open with a barrier so both
processes have read BEFORE either writes — the interleaving is the point, not a lucky schedule.

The TOCTOU tests pin BOTH halves of the contract: exactly one winner, and the loser gets an HONEST
error. A fix that let both "succeed" by merging, or that silently dropped the loser, would pass a
naive "no corruption" check while destroying a session.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import inspect
import json
import multiprocessing as mp
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import flush_liveness, session_bundle, session_transport, team_health, vault, visibility
from mokata.atomicfile import atomic_write_text, lock_path_for
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.govern.revert import RevertError, ReversibleStateStore, gated_reversible_write
from mokata.memory.store import MemoryStore
from mokata.state import StateStore

_CTX = mp.get_context("spawn")  # stable across POSIX/Windows; children re-import cleanly

WRITES_PER_CHILD = 12


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


class _FakeSurface:
    """A minimal surface for the liveness state path (module-level so `spawn` can pickle it)."""

    def __init__(self, mokata_dir):
        self.mokata_dir = mokata_dir


# ----------------------------------------------------------------- spawn-safe worker functions
def _undo_worker(root, wid, n, go):
    """One window: build its OWN ReversibleStateStore (a fresh undo-log read, exactly as a second
    Claude Code window would) and make `n` reversible writes to keys only it owns."""
    from mokata.govern.revert import ReversibleStateStore
    from mokata.state import StateStore
    rs = ReversibleStateStore(StateStore(root))
    go.wait(timeout=20)
    for i in range(n):
        rs.write(f"k{wid}_{i}", {"w": wid, "i": i})


def _liveness_worker(mokata_dir, n, go):
    from mokata import flush_liveness as fl

    def _bump(state):
        state.attempts += 1
        return state

    surface = _FakeSurface(mokata_dir)
    go.wait(timeout=20)
    for _ in range(n):
        fl.update_state(surface, _bump)


def _stats_worker(root, n, go):
    from mokata.config import Surface
    from mokata.memory.store import MemoryStore
    store = MemoryStore.from_surface(Surface.load(root))
    go.wait(timeout=20)
    for _ in range(n):
        store.all_active()                      # the public read path; bumps `reads` by exactly 1


def _vault_claim_worker(root, wid, planned, q):
    """Two windows racing to push DIFFERENT content under the SAME vault name."""
    from mokata import vault as V
    src = os.path.join(root, f"src{wid}.md")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(f"# design\n\nfrom window {wid}\n")
    plan = V.plan_push(root, "design", src)
    planned.wait(timeout=20)                    # BOTH have planned ("new") before EITHER commits
    try:
        entry = V.commit_push(root, plan, author=f"w{wid}")
        q.put((wid, "won", entry.content_hash))
    except V.VaultError as exc:
        q.put((wid, "lost", str(exc)))


def _bundle_claim_worker(root, wid, planned, q):
    """Two windows racing to push DIFFERENT sessions under the SAME bundle tag."""
    from mokata import session_bundle as SB
    from mokata.config import Surface
    from mokata.govern.resume import PipelineCheckpoint
    surface = Surface.load(root)
    run_id = f"run-{wid}"
    PipelineCheckpoint(surface.state, run_id).mark_passed("brainstorm")
    plan = SB.plan_session_push(root, surface, "shared", run_id=run_id)
    planned.wait(timeout=20)                    # BOTH planned ("new") before EITHER commits
    try:
        SB.commit_session_push(plan)
        q.put((wid, "won", plan.bundle["content_hash"]))
    except SB.SessionBundleError as exc:
        q.put((wid, "lost", str(exc)))


def _run(target, args_list):
    procs = [_CTX.Process(target=target, args=a) for a in args_list]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
    for p in procs:
        assert p.exitcode == 0, f"child exited {p.exitcode}"


def _drain(q, n):
    return [q.get(timeout=30) for _ in range(n)]


# =============================================================== the M-6 regression (undo log)
class UndoLogRMWTest(unittest.TestCase):

    def test_m6_regression_two_processes_hammer_the_undo_log(self):
        """THE M-6 regression. Two real windows interleave reversible writes; every revert point
        from BOTH survives. Before the fix each window wrote back the whole log from its own stale
        in-memory copy, so the loser's revert points vanished — silently, and only discoverable at
        the moment someone needed to undo."""
        with tempfile.TemporaryDirectory() as d:
            go = _CTX.Event()
            procs = [_CTX.Process(target=_undo_worker, args=(d, wid, WRITES_PER_CHILD, go))
                     for wid in (0, 1)]
            for p in procs:
                p.start()
            go.set()                                    # release both at once
            for p in procs:
                p.join(timeout=60)
            for p in procs:
                self.assertEqual(p.exitcode, 0)

            # the file is VALID (never torn by a concurrent replace)
            with open(os.path.join(d, "undo_log.json"), encoding="utf-8") as fh:
                raw = json.load(fh)
            records = raw["records"]

            # NOT ONE undo entry lost, and both windows' revert points are present
            self.assertEqual(len(records), 2 * WRITES_PER_CHILD)
            targets = {r["target"] for r in records}
            for wid in (0, 1):
                for i in range(WRITES_PER_CHILD):
                    self.assertIn(f"k{wid}_{i}", targets)

            # and every recorded point still reverts (the log is usable, not just well-formed)
            rs = ReversibleStateStore(StateStore(d))
            rec = rs.revert("k0_3")
            self.assertEqual(rec.target, "k0_3")
            self.assertFalse(StateStore(d).exists("k0_3"))   # before was None → key removed

    def test_a_reversible_write_captures_before_under_the_same_lock(self):
        """The subtler half: `before` capture, target write, and undo append are ONE critical
        section. Interleaved, two windows could both record `before=X` when one had already moved
        the key to X1 — reverting would then restore X and silently destroy X1. Pinned here by the
        chain being internally consistent after concurrent writes to the SAME key."""
        with tempfile.TemporaryDirectory() as d:
            go = _CTX.Event()
            # both windows write the SAME key, interleaved
            procs = [_CTX.Process(target=_same_key_worker, args=(d, wid, 8, go)) for wid in (0, 1)]
            for p in procs:
                p.start()
            go.set()
            for p in procs:
                p.join(timeout=60)
            for p in procs:
                self.assertEqual(p.exitcode, 0)

            with open(os.path.join(d, "undo_log.json"), encoding="utf-8") as fh:
                records = json.load(fh)["records"]
            self.assertEqual(len(records), 16)           # no lost entries

            # the chain is CONSISTENT: each record's `before` is the previous record's `after`
            # (the property an interleaved before-capture destroys).
            for prev, nxt in zip(records, records[1:]):
                self.assertEqual(nxt["before"], prev["after"])

    def test_single_process_revert_round_trip_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(d)
            rs = ReversibleStateStore(store)
            rs.write("cfg", {"v": 1})
            rs.write("cfg", {"v": 2})
            self.assertEqual(store.read("cfg"), {"v": 2})
            rec = rs.revert()                            # most recent write
            self.assertEqual(rec.before, {"v": 1})
            self.assertEqual(store.read("cfg"), {"v": 1})
            rs.revert()                                  # back to nothing
            self.assertFalse(store.exists("cfg"))
            with self.assertRaises(RevertError):
                rs.revert()

    def test_an_empty_revert_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(d)
            rs = ReversibleStateStore(store)
            with self.assertRaises(RevertError):
                rs.revert()
            self.assertFalse(store.exists("undo_log"))   # the failed RMW wrote no file

    def test_gated_reversible_write_does_not_deadlock_on_the_ledger(self):
        """Lock ORDER pin. The WriteGate holds the ledger lock across its `commit()`, and the commit
        runs through this store — so the gated path is ledger → undo. If the undo lock were held
        while recording to the ledger, the reverse edge would exist and this would hang until
        LockTimeout. It must simply complete."""
        from mokata.govern import AuditLedger, WriteGate, WriteRequest
        with tempfile.TemporaryDirectory() as d:
            ledger = AuditLedger.from_mokata_dir(d)
            store = StateStore(os.path.join(d, "state"))
            rs = ReversibleStateStore(store, ledger=ledger)
            gate = WriteGate(ledger=ledger)
            outcome, rec = gated_reversible_write(
                gate, rs, WriteRequest("config", "cfg", content="{}"), {"v": 1}, assume_yes=True)
            self.assertTrue(outcome.committed)
            self.assertEqual(rec.target, "cfg")
            self.assertEqual(store.read("cfg"), {"v": 1})
            self.assertEqual(rs.revert().before, None)   # the undo point survived the gated path


def _same_key_worker(root, wid, n, go):
    from mokata.govern.revert import ReversibleStateStore
    from mokata.state import StateStore
    rs = ReversibleStateStore(StateStore(root))
    go.wait(timeout=20)
    for i in range(n):
        rs.write("shared", {"w": wid, "i": i})


# =============================================================== liveness state (MS.S5 grounding d)
class LivenessRMWTest(unittest.TestCase):

    def test_two_processes_updating_liveness_lose_no_attempts(self):
        """The retry cap is the ONLY thing bounding retries against a dead DB. A lost `attempts`
        increment pushes the cap out — with enough interleaving, out of reach — which is precisely
        the silent hammering RETRY_CAP exists to prevent."""
        with tempfile.TemporaryDirectory() as d:
            go = _CTX.Event()
            procs = [_CTX.Process(target=_liveness_worker, args=(d, WRITES_PER_CHILD, go))
                     for _ in (0, 1)]
            for p in procs:
                p.start()
            go.set()
            for p in procs:
                p.join(timeout=60)
            for p in procs:
                self.assertEqual(p.exitcode, 0)

            state = flush_liveness.load_state(_FakeSurface(d))
            self.assertEqual(state.attempts, 2 * WRITES_PER_CHILD)   # not one increment lost

    def test_liveness_state_file_is_never_torn(self):
        """The badge and `mokata doctor` read this file from SEPARATE processes while a flush may be
        writing it. A half-written file reads as "no backlog" — hiding the very thing it surfaces."""
        with tempfile.TemporaryDirectory() as d:
            surface = _FakeSurface(d)
            st = flush_liveness.LivenessState(attempts=3, next_retry_at=99.0,
                                              last_failure_class="unreachable", backlog_since=5.0)
            flush_liveness.store_state(surface, st)
            back = flush_liveness.load_state(surface)
            self.assertEqual(back.to_dict(), st.to_dict())           # byte-for-byte round trip
            path = os.path.join(d, "temp_local", flush_liveness.STATE_FILENAME)
            self.assertTrue(os.path.exists(path))
            # no temp/lock debris left in the directory listing
            leftovers = [f for f in os.listdir(os.path.dirname(path)) if f.startswith(".tmp-")]
            self.assertEqual(leftovers, [])


# =============================================================== memory_stats (shared counters)
class MemoryStatsRMWTest(unittest.TestCase):

    def test_two_processes_bumping_stats_lose_no_counts(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            # self-calibrate: whatever one store costs to construct + N reads, two must total twice
            probe = MemoryStore.from_surface(surface)
            for _ in range(WRITES_PER_CHILD):
                probe.all_active()
            per_child = surface.state.read("memory_stats")["reads"]
            surface.state.write("memory_stats", {"reads": 0, "writes": 0})

            go = _CTX.Event()
            procs = [_CTX.Process(target=_stats_worker, args=(d, WRITES_PER_CHILD, go))
                     for _ in (0, 1)]
            for p in procs:
                p.start()
            go.set()
            for p in procs:
                p.join(timeout=60)
            for p in procs:
                self.assertEqual(p.exitcode, 0)

            total = Surface.load(d).state.read("memory_stats")["reads"]
            self.assertEqual(total, 2 * per_child)       # both windows' reads counted, none erased


# =============================================================== TOCTOU: vault + session bundles
class VaultToctouTest(unittest.TestCase):

    def test_two_processes_racing_one_vault_name_produce_exactly_one_winner(self):
        with tempfile.TemporaryDirectory() as d:
            planned, q = _CTX.Barrier(2), _CTX.Queue()
            _run(_vault_claim_worker, [(d, 0, planned, q), (d, 1, planned, q)])
            results = _drain(q, 2)

            won = [r for r in results if r[1] == "won"]
            lost = [r for r in results if r[1] == "lost"]
            self.assertEqual(len(won), 1, f"expected exactly one winner: {results}")
            self.assertEqual(len(lost), 1, f"expected exactly one honest loser: {results}")

            # the loser's error is HONEST — it names the clash and says nothing was clobbered
            self.assertIn("claimed by another window", lost[0][2])
            self.assertIn("nothing clobbered", lost[0][2])

            # the index is valid, holds ONE entry, and it is the WINNER's content — not a merge,
            # not the loser's, not a half-written file
            index = vault.load_index(d)
            self.assertEqual(list(index["entries"]), ["design"])
            self.assertEqual(index["entries"]["design"]["content_hash"], won[0][2])
            content, entry = vault.vault_pull(d, "design")        # verifies the content hash
            self.assertEqual(vault.content_hash(content), won[0][2])
            self.assertEqual(entry.version, 1)

    def test_a_forced_push_still_versions_and_keeps_the_prior_trail(self):
        """The claim re-check must not break `--force`, which is the SANCTIONED overwrite."""
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.md"), os.path.join(d, "b.md")
            for p, text in ((a, "# one\n"), (b, "# two\n")):
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(text)
            vault.commit_push(d, vault.plan_push(d, "design", a), author="x")
            plan = vault.plan_push(d, "design", b, force=True)
            self.assertEqual(plan.status, "version")
            entry = vault.commit_push(d, plan, author="y")
            self.assertEqual(entry.version, 2)
            self.assertEqual(len(entry.history), 1)              # the prior version is in the trail
            self.assertEqual(entry.history[0]["version"], 1)

    def test_an_unforced_push_over_changed_content_is_still_refused_at_plan_time(self):
        """The pre-existing conflict path is untouched (the fix ADDS a commit-time claim; it does
        not move the gate)."""
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.md"), os.path.join(d, "b.md")
            for p, text in ((a, "# one\n"), (b, "# two\n")):
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(text)
            vault.commit_push(d, vault.plan_push(d, "design", a))
            plan = vault.plan_push(d, "design", b)
            self.assertEqual(plan.status, "conflict")
            self.assertTrue(plan.blocked)
            with self.assertRaises(vault.VaultError):
                vault.commit_push(d, plan)


class BundleToctouTest(unittest.TestCase):

    def test_two_processes_racing_one_bundle_tag_produce_exactly_one_winner(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            planned, q = _CTX.Barrier(2), _CTX.Queue()
            _run(_bundle_claim_worker, [(d, 0, planned, q), (d, 1, planned, q)])
            results = _drain(q, 2)

            won = [r for r in results if r[1] == "won"]
            lost = [r for r in results if r[1] == "lost"]
            self.assertEqual(len(won), 1, f"expected exactly one winner: {results}")
            self.assertEqual(len(lost), 1, f"expected exactly one honest loser: {results}")
            self.assertIn("claimed by another window", lost[0][2])
            self.assertIn("nothing clobbered", lost[0][2])

            # exactly one bundle on disk, it PARSES (hash-verified, so not torn/merged), and it is
            # the winner's session
            t = session_transport.LocalTransport(d)
            self.assertEqual(t.list_tags(), ["shared"])          # the lock file is NOT listed
            bundle = session_bundle.parse_bundle(t.read_bundle("shared"))
            self.assertEqual(bundle["content_hash"], won[0][2])

    def test_a_rename_onto_a_name_claimed_mid_gate_is_refused_not_clobbered(self):
        """The rename's `collision` status is computed at plan time; the gate then runs. This pins
        the commit-time re-claim: a name taken during the gate is refused, never overwritten."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = Surface.load(d)
            PipelineCheckpoint(surface.state, "r1").mark_passed("brainstorm")
            t = session_transport.LocalTransport(d)
            session_bundle.commit_session_push(
                session_bundle.plan_session_push(d, surface, "old", run_id="r1"))

            plan = session_bundle.plan_session_rename(d, "old", "new")
            self.assertEqual(plan.status, "ok")                  # "new" was free when planned

            # ... a sibling window claims "new" while the human is at the gate
            t.write_bundle("new", t.read_bundle("old").replace('"old"', '"sibling"'))
            claimed = t.read_bundle("new")

            with self.assertRaises(session_bundle.SessionBundleError) as cm:
                session_bundle.commit_session_rename(plan)
            self.assertIn("claimed by another window", str(cm.exception))
            self.assertEqual(t.read_bundle("new"), claimed)      # untouched
            self.assertIsNotNone(t.read_bundle("old"))           # and the source is still there

    def test_a_forced_rename_still_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            surface = Surface.load(d)
            PipelineCheckpoint(surface.state, "r1").mark_passed("brainstorm")
            session_bundle.commit_session_push(
                session_bundle.plan_session_push(d, surface, "old", run_id="r1"))
            t = session_transport.LocalTransport(d)
            t.write_bundle("new", t.read_bundle("old"))

            plan = session_bundle.plan_session_rename(d, "old", "new", force=True)
            self.assertEqual(plan.status, "ok")
            self.assertTrue(plan.forced)
            session_bundle.commit_session_rename(plan)           # sanctioned overwrite: no raise
            self.assertIsNone(t.read_bundle("old"))              # moved, not copied


# =============================================================== the shared primitive
class AtomicFileTest(unittest.TestCase):

    def test_the_lock_handle_is_never_the_target_and_never_looks_like_an_artifact(self):
        """`os.replace` swaps the inode out from under a lock held on the target, which silently
        defeats mutual exclusion — so the handle is a dot-prefixed `.lock` SIBLING, invisible to the
        `*.json` scans that list bundles/state."""
        lp = lock_path_for("/repo/.mokata/vault/index.json")
        # `lock_path_for` builds a real filesystem path with `os.path.join`, so its separator is
        # os-native — a backslash on Windows. That is CORRECT (the path is only ever opened by
        # `file_lock`, never compared as a canonical key), so the expectation is built the same
        # way rather than pinned to a POSIX literal.
        self.assertEqual(lp, os.path.join("/repo/.mokata/vault", ".index.json.lock"))
        self.assertNotEqual(lp, "/repo/.mokata/vault/index.json")
        self.assertFalse(lp.endswith(".json"))
        self.assertTrue(os.path.basename(lp).startswith("."))

    def test_a_failed_write_leaves_the_previous_content_and_no_debris(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.json")
            atomic_write_text(path, '{"v": 1}')
            with self.assertRaises(TypeError):
                atomic_write_text(path, object())             # type: ignore[arg-type]
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), '{"v": 1}')      # old content intact
            self.assertEqual([f for f in os.listdir(d) if f.startswith(".tmp-")], [])

    def test_statestore_output_is_byte_identical_after_the_extraction(self):
        """MS.S1's on-disk behaviour must not move: the state store delegates its write half here,
        passing its own temp-file naming through."""
        with tempfile.TemporaryDirectory() as d:
            StateStore(d).write("k", {"b": 2, "a": 1})
            with open(os.path.join(d, "k.json"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), '{\n  "b": 2,\n  "a": 1\n}\n')   # indent=2, insertion order
            self.assertEqual([f for f in os.listdir(d) if f.startswith(".tmp-")], [])


# =============================================================== sweep completeness (grep-guard)
class SweepGuardTest(unittest.TestCase):
    """A regression guard on the SWEEP itself: the shared artifacts classified RACY-SHARED must keep
    routing through the safe helpers. Scoped to those exact writers, so it never fights a legitimate
    plain writer (an export to a user-chosen destination, a rendered dashboard, a plan file)."""

    # every function that actually WRITES a RACY-SHARED artifact (the RMW wrappers that delegate
    # their write — vault.update_index → _save_index — are pinned by the lock guard below instead)
    GUARDED = [
        (vault._save_index, "vault index"),
        (vault.commit_push, "vault artifact + index"),
        (session_transport._FileTransport.write_bundle, "session bundle"),
        (flush_liveness.store_state, "flush liveness"),
        (flush_liveness.update_state, "flush liveness RMW"),
        (team_health.store, "team health cache"),
        (visibility.capture_session_snapshot, "session snapshot"),
        (StateStore._atomic_write, "state store"),
    ]

    def test_no_shared_artifact_is_written_with_a_plain_open_w(self):
        for func, label in self.GUARDED:
            with self.subTest(artifact=label):
                src = inspect.getsource(func)
                self.assertNotIn('"w"', src, f"{label}: plain open(..., 'w') is back")
                self.assertNotIn("json.dump(", src, f"{label}: unguarded json.dump is back")
                self.assertIn("atomic_write_text", src, f"{label}: does not write atomically")

    def test_every_read_modify_write_holds_a_cross_process_lock(self):
        """Atomicity alone is NOT enough: an atomic write from a stale read still loses the other
        window's update. Each RMW must hold the lock ACROSS read → modify → replace."""
        rmw = [
            (vault.update_index, "index_lock"),         # ... which is itself an oslock file_lock
            (vault.commit_push, "index_lock"),
            (flush_liveness.update_state, "file_lock"),
            (StateStore.update, "file_lock"),
        ]
        for func, primitive in rmw:
            with self.subTest(rmw=func.__qualname__):
                self.assertIn(primitive, inspect.getsource(func))
        # and the vault's index_lock is the SHARED oslock primitive, not a second mechanism
        self.assertIn("file_lock", inspect.getsource(vault.index_lock))
        self.assertIn("atomic_write_text", inspect.getsource(vault._save_index))

    def test_the_undo_log_and_stats_ride_the_locked_state_store_rmw(self):
        """Store-shaped artifacts route through `StateStore.update` (the MS.S1 primitive) rather
        than a second mechanism."""
        for func in (ReversibleStateStore.write, ReversibleStateStore.revert):
            src = inspect.getsource(func)
            self.assertIn("self.store.update(", src)
            self.assertIn("_op_lock", src)
        self.assertIn("store.update(", inspect.getsource(MemoryStore._persist_stats))

    def test_toctou_claims_are_re_checked_under_the_lock(self):
        """A claim decided at plan time and trusted at commit time IS the TOCTOU bug. Both commits
        must re-read the store and refuse an occupied name."""
        for func in (session_bundle.commit_session_push, session_bundle.commit_session_rename):
            src = inspect.getsource(func)
            self.assertIn("_tag_lock", src)
            self.assertIn("claimed by another window", src)
        self.assertIn("claimed by another window", inspect.getsource(vault._claim))


if __name__ == "__main__":
    unittest.main()
