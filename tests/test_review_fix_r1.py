"""REVIEW-FIX.R1 — session-aware review-verdict keying + fail-closed run-less read (0.0.16).

The bug (SHIP-SAFETY): the review verdict was recorded and read under `progress.find_active_run`,
which is session-BLIND (it scans every `pipeline_run__*` checkpoint in the shared state root) and
RE-ANSWERS as run state changes. So the record-key and the read-key were not the same run, and the
read had a second hole: `latest_review_verdict(run_id=None)` filtered NOTHING (`if run_id is not
None and …`), so the last verdict in the whole stream won. Together: after `/clear` — or with two
runs on disk — `/ship`'s `review-status` check could pass the gate on a FOREIGN run's review, or on
a run-less/stale verdict. Ship-on-stale-review.

The fix under test:
  1. record + read resolve the run through ONE session-aware resolver (`badge_run.resolve_verdict_run`
     — bound run -> the run the gate hook would enforce -> None), so record-key == read-key;
  2. `--run` on BOTH `mokata progress record-review` and `review-status` (the explicit key);
  3. FAIL CLOSED: no resolvable run ⇒ `ship_review_gate` BLOCKS with the remedy, and
     `latest_review_verdict(run_id=None)` REFUSES instead of scanning every run's verdicts;
  4. ordering: resolution ignores B-LIFE ship retirement, so `mokata progress mark ship` (recorded
     on ENTRY by ship.md, before the read) cannot re-key the gate — the read is order-independent;
  5. fold-in: `list_runs_by_recency` — "most recent run" is checkpoint mtime, not the lexicographic
     order of `uuid4().hex` ids;
  6. negatives: the badge path, the single-run happy path and the on-disk record format unchanged.

Business-level asserts: what `/ship` observes (the `review-status` exit code + line, and the
`ReviewGate` ship reads), never implementation poking.
"""

import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import progress, session_registry as SR
from mokata.badge_run import bind_session_run, resolve_badge_run, resolve_verdict_run
from mokata.config import Surface
from mokata.govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from mokata.progress import find_active_run, list_runs, list_runs_by_recency
from mokata.progress_events import (
    ENVELOPE_KEYS,
    REVIEW_VERDICT,
    STAGE_ENTER,
    ProgressLog,
    latest_review_verdict,
    record_review_verdict,
    ship_review_gate,
)
from mokata.state import StateStore
from mokata.tdd_state import state_dir

PHASES = ("brainstorm", "analysis", "strawman", "pre_mortem", "probes",
          "completeness_gate", "emit")


# --------------------------------------------------------------------------- fixtures
def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _persist_run(root, run_id, passed=()):
    """A run with a persisted checkpoint on disk (NOT live — no registry entry): what a run left
    behind by a `/clear`ed or closed window looks like."""
    cp = PipelineCheckpoint(Surface.load(root).state, run_id)
    cp.ensure_registered()
    for p in passed:
        cp.mark_passed(p)
    return cp


def _register_live_run(root, run_id, passed=()):
    """A run that is persisted AND live: its id sits in the MS.S2 registry with an alive pid rooted
    here — exactly what the R-MCP narrowing (`gate_hook._live_runs`) survives on."""
    _persist_run(root, run_id, passed)
    store = StateStore(state_dir(root))
    reg = store.read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}
    reg.setdefault("sessions", {})[run_id] = {
        "session_id": run_id, "started_at": "2026-07-26T00:00:00Z", "pid": os.getpid(),
        "repo_root": os.path.realpath(root), "last_seen": "2026-07-26T00:00:00Z",
        "phase": None, "scope": None,
    }
    store.write(SR.SESSION_REGISTRY_KEY, reg)


def _mark_ship(surface, run_id):
    """Retire `run_id` EXACTLY as `mokata progress mark ship` does (cmd_progress_mark): append the
    terminal `stage_enter: ship` event + stamp `completed_at`."""
    ProgressLog.from_surface(surface).append_event(STAGE_ENTER, "ship", run_id=run_id)
    PipelineCheckpoint(surface.state, run_id).mark_completed()


def _set_mtime(root, run_id, when):
    p = os.path.join(state_dir(root), CHECKPOINT_PREFIX + run_id + ".json")
    os.utime(p, (when, when))


def _old_gate_would_pass(surface):
    """Whether the PRE-R1 gate would have PASSED — a verbatim reconstruction of the two lines the
    stage replaced, so the leak is pinned by this suite instead of by a memory of the old build:

        run_id = find_active_run(surface.state)        # progress_events.py:265-266 (session-blind)
        for e in events:                              # progress_events.py:208-215
            if run_id is not None and e.get("run_id") != run_id: continue   # None => NO filtering

    True means the old code let ship proceed on whatever verdict that scan happened to end on."""
    run_id = find_active_run(surface.state)
    found = None
    for e in ProgressLog.from_surface(surface).read_events():
        if e.get("type") != REVIEW_VERDICT:
            continue
        if run_id is not None and e.get("run_id") != run_id:
            continue
        data = e.get("data")
        if isinstance(data, dict):
            found = data
    return bool(found and found.get("passed"))


def _cli(argv, cwd):
    """Run the CLI as ship/review do (a separate process's argv), returning (rc, stdout)."""
    from mokata.cli import main
    out = io.StringIO()
    old = os.getcwd()
    os.chdir(cwd)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = main(argv)
    finally:
        os.chdir(old)
    return rc, out.getvalue()


class _NoPinnedSession(unittest.TestCase):
    """`MOKATA_SESSION_ID` pins run resolution (gate_hook.resolve_run) — a pin leaking in from the
    environment would silently un-ambiguate these scenarios, so every test runs without one."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("MOKATA_SESSION_ID", None)


# ======================================================= THE regression (pins the leak)
class TestReviewFixR1Regression(_NoPinnedSession):
    def test_review_fix_r1_regression(self):
        """Two runs on disk + a `/clear`-shaped session (no binding, no live window):

        runF (another window's run, mid-pipeline) recorded a PASSED review. runM — the run being
        shipped — recorded a FAILED one. The OLD gate resolved the session-blind "first incomplete
        run" (runF) and passed ship on runF's review; the NEW gate refuses to guess and BLOCKS, and
        naming the run reports runM's own FAILED verdict."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "runM", passed=PHASES)              # mine: complete, at review/ship
            _persist_run(d, "runF", passed=PHASES[:1])          # foreign: mid-pipeline
            record_review_verdict(surface, passed=True, independent=True, run_id="runF")
            record_review_verdict(surface, passed=False, independent=True, run_id="runM")

            # PIN THE LEAK — old resolution lands on the FOREIGN run and its PASS satisfies ship.
            self.assertEqual(find_active_run(surface.state), "runF")
            self.assertTrue(_old_gate_would_pass(surface),
                            "pre-R1 code shipped on the foreign run's review")

            # NEW — two runs, nothing to narrow with: refuse, with the remedy named.
            gate = ship_review_gate(surface)
            self.assertTrue(gate.blocks)
            self.assertFalse(gate.present)
            self.assertIn("no run to attribute it to", gate.message)
            self.assertIn("--run", gate.unblock)

            # NEW — named explicitly, ship sees runM's OWN verdict: it failed.
            named = ship_review_gate(surface, run_id="runM")
            self.assertTrue(named.blocks)
            self.assertIn("review failed", named.message)

    def test_runless_verdict_no_longer_satisfies_a_global_scan(self):
        """The second half of the leak: with NO run on disk the old read filtered nothing, so a
        run-less verdict in the stream passed the gate. Now the read refuses."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            record_review_verdict(surface, passed=True, independent=True, run_id=None)
            self.assertIsNone(find_active_run(surface.state))
            self.assertTrue(_old_gate_would_pass(surface), "pre-R1 code shipped on it")

            self.assertTrue(ship_review_gate(surface).blocks)
            self.assertIsNone(latest_review_verdict(surface, run_id=None))


# ======================================================= the four spec'd cases
class TestSpecdCases(_NoPinnedSession):
    def test_clear_then_no_stale_verdict(self):
        """/clear: the previous run's PASSED review must not satisfy the next session's ship."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "runA", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True, run_id="runA")
            _mark_ship(surface, "runA")                       # A finished and was retired
            _persist_run(d, "runB", passed=PHASES[:1])        # the cleared session's fresh run

            self.assertTrue(ship_review_gate(surface).blocks)          # ambiguous -> refuse

            # SessionStart bound the cleared window to its OWN run: the gate keys on runB, which
            # has no verdict — "review hasn't run", never runA's stale pass.
            bind_session_run(d, "sessB", "runB")
            self.assertEqual(resolve_verdict_run(d, "sessB"), "runB")
            gate = ship_review_gate(surface, run_id=resolve_verdict_run(d, "sessB"))
            self.assertTrue(gate.blocks)
            self.assertIn("review hasn't run", gate.message)

    def test_two_runs_no_cross_attribution(self):
        """Each run reads its OWN verdict; neither reads the other's."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "run-1", passed=PHASES)
            _persist_run(d, "run-2", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True, run_id="run-1")

            self.assertFalse(ship_review_gate(surface, run_id="run-1").blocks)
            blocked = ship_review_gate(surface, run_id="run-2")
            self.assertTrue(blocked.blocks)
            self.assertIn("review hasn't run", blocked.message)

    def test_runless_read_blocks_instead_of_global_scan(self):
        """No resolvable run ⇒ BLOCK naming the remedy — never a scan of every run's verdicts."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "x-run", passed=PHASES)
            _persist_run(d, "y-run", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True, run_id="x-run")
            record_review_verdict(surface, passed=True, independent=True, run_id="y-run")

            gate = ship_review_gate(surface)          # both runs pass — still refuses to pick
            self.assertTrue(gate.blocks)
            self.assertIn("another run's verdict", gate.message)
            self.assertIn("mokata sessions", gate.unblock)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 2)
            self.assertIn("--run", out)

    def test_ship_after_review_passes(self):
        """The happy path still ships: one run, verdict recorded and read with no explicit id."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _register_live_run(d, "solo", passed=PHASES)
            rc, out = _cli(["progress", "record-review", "--passed", "--independent",
                            "--path", d], d)
            self.assertEqual(rc, 0)
            self.assertIn("for run solo", out)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 0)
            self.assertIn("review passed (independent ✓)", out)


# ======================================================= 1 · record-key == read-key
class TestRecordKeyEqualsReadKey(_NoPinnedSession):
    def test_record_and_read_resolve_the_same_run(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "only-run", passed=PHASES)
            event = record_review_verdict(surface, passed=True, independent=True)
            self.assertEqual(event["run_id"], "only-run")
            self.assertEqual(event["run_id"], resolve_verdict_run(d))
            self.assertFalse(ship_review_gate(surface).blocks)

    def test_resolution_is_not_session_blind(self):
        """The resolver refuses where `find_active_run` answers — the whole difference."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "runA", passed=PHASES[:1])
            _persist_run(d, "runB", passed=PHASES[:1])
            self.assertIsNotNone(find_active_run(Surface.load(d).state))   # blind: answers anyway
            self.assertIsNone(resolve_verdict_run(d))                      # aware: refuses

    def test_bound_session_beats_the_other_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "runA", passed=PHASES)
            _persist_run(d, "runB", passed=PHASES)
            bind_session_run(d, "sessA", "runA")
            self.assertEqual(resolve_verdict_run(d, "sessA"), "runA")
            self.assertEqual(resolve_verdict_run(d, "sessB"), None)  # unbound, still ambiguous

    def test_live_window_narrows_two_runs_on_disk(self):
        """R-MCP narrowing: one dead run + one LIVE run ⇒ the live one is this session's."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "dead-run", passed=PHASES)
            _register_live_run(d, "live-run", passed=PHASES)
            event = record_review_verdict(surface, passed=True, independent=True)
            self.assertEqual(event["run_id"], "live-run")
            self.assertFalse(ship_review_gate(surface).blocks)


# ======================================================= 2 · --run through the CLI
class TestCliRunFlag(_NoPinnedSession):
    def test_cli_run_flag_records_and_reads_that_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "run-1", passed=PHASES)
            _persist_run(d, "run-2", passed=PHASES)
            rc, out = _cli(["progress", "record-review", "--passed", "--independent",
                            "--run", "run-1", "--path", d], d)
            self.assertEqual(rc, 0)
            self.assertIn("for run run-1", out)

            rc, out = _cli(["progress", "review-status", "--run", "run-1", "--path", d], d)
            self.assertEqual(rc, 0)
            self.assertIn("independent ✓", out)

            rc, out = _cli(["progress", "review-status", "--run", "run-2", "--path", d], d)
            self.assertEqual(rc, 2)
            self.assertIn("review hasn't run", out)

    def test_runless_record_says_ship_will_not_accept_it(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)                                   # no run at all -> nothing to key to
            rc, out = _cli(["progress", "record-review", "--passed", "--path", d], d)
            self.assertEqual(rc, 0)                    # recording stays best-effort (unchanged)
            self.assertIn("WITHOUT a run", out)
            self.assertIn("--run", out)


# ======================================================= 3 · fail closed on a run-less read
class TestFailClosed(_NoPinnedSession):
    def test_latest_review_verdict_refuses_a_runless_read(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            record_review_verdict(surface, passed=True, independent=True, run_id="some-run")
            self.assertIsNone(latest_review_verdict(surface, run_id=None),
                              "a run-less read must refuse, not scan every run's verdicts")
            self.assertIsNotNone(latest_review_verdict(surface, run_id="some-run"))

    def test_block_message_names_what_to_run(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            gate = ship_review_gate(surface)
            self.assertTrue(gate.blocks)
            self.assertIn("mokata progress review-status --run", gate.unblock)
            self.assertIn("/mokata:brainstorm", gate.unblock)

    def test_unreadable_surface_still_blocks(self):
        class Broken:
            state = None
            root = None
        gate = ship_review_gate(Broken())
        self.assertTrue(gate.blocks)


# ======================================================= 4 · ordering is irrelevant
class TestOrderingIrrelevant(_NoPinnedSession):
    def test_verdict_survives_the_ship_entry_mark(self):
        """ship.md records `mark ship` on ENTRY, before it reads `review-status`. The verdict is
        keyed to the resolved run, and resolution ignores B-LIFE retirement, so the read is
        byte-identical before and after the mark — the ordering cannot re-key the gate."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _register_live_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            before = ship_review_gate(surface)
            _mark_ship(surface, "solo")                       # retires the run for the BADGE
            after = ship_review_gate(Surface.load(d))
            self.assertEqual((after.blocks, after.message), (before.blocks, before.message))
            self.assertFalse(after.blocks)

            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 0)                           # ship still reads its own pass
            self.assertIn("independent ✓", out)

    def test_retired_run_still_badges_clean(self):
        """The same retirement the verdict read ignores is STILL applied to the badge (display)."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _register_live_run(d, "solo", passed=PHASES)
            _mark_ship(surface, "solo")
            self.assertIsNone(resolve_badge_run(d, "sessX"))          # badge: retired
            self.assertEqual(resolve_verdict_run(d, "sessX"), "solo")  # verdict: still keyed


# ======================================================= 5 · fold-in: real recency
class TestRecencyFoldIn(_NoPinnedSession):
    def test_list_runs_by_recency_is_mtime_not_lexicographic(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "aaa", passed=PHASES)
            _persist_run(d, "zzz", passed=PHASES)
            _set_mtime(d, "zzz", 1_600_000_000)       # zzz is OLD, aaa is new
            _set_mtime(d, "aaa", 1_700_000_000)
            store = Surface.load(d).state
            self.assertEqual(list_runs(store), ["aaa", "zzz"])              # by name, unchanged
            self.assertEqual(list_runs_by_recency(store), ["zzz", "aaa"])   # oldest -> newest

    def test_most_recent_fallback_is_the_newest_run_not_the_highest_id(self):
        """`find_active_run`'s completed-but-unshipped fallback claims "most recent"; run ids are
        `uuid4().hex`, so the old `reversed(list_runs(...))` picked an arbitrary run."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "aaa", passed=PHASES)
            _persist_run(d, "zzz", passed=PHASES)
            _set_mtime(d, "zzz", 1_600_000_000)
            _set_mtime(d, "aaa", 1_700_000_000)
            self.assertEqual(find_active_run(Surface.load(d).state), "aaa")

    def test_last_run_message_names_the_newest_shipped_run(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "aaa", passed=PHASES)
            _persist_run(d, "zzz", passed=PHASES)
            _mark_ship(surface, "aaa")
            _mark_ship(surface, "zzz")
            _set_mtime(d, "zzz", 1_600_000_000)
            _set_mtime(d, "aaa", 1_700_000_000)
            msg = progress.build_progress(Surface.load(d).state).message
            self.assertIn("last run 'aaa'", msg)


# ======================================================= 6 · the negatives
class TestNoBehaviourChange(_NoPinnedSession):
    def test_single_run_happy_path_is_byte_identical(self):
        """One run in the repo: the resolved key, the recorded event and the gate line are exactly
        what the pre-R1 (find_active_run) path produced."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            blind = find_active_run(surface.state)
            event = record_review_verdict(surface, passed=True, independent=True)
            self.assertEqual(event["run_id"], blind)
            self.assertEqual(event["data"], {"passed": True, "independent": True})
            gate = ship_review_gate(surface)
            self.assertEqual(gate.message, "review passed (independent ✓)")
            self.assertFalse(gate.blocks)
            self.assertTrue(gate.present and gate.passed and gate.independent)

    def test_inline_pass_still_ships_and_failed_still_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=False)
            self.assertIn("inline — not independent", ship_review_gate(surface).message)
            record_review_verdict(surface, passed=False, independent=True)
            self.assertIn("review failed", ship_review_gate(surface).message)

    def test_record_format_on_disk_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=False, independent=True, findings=3)
            events = [e for e in ProgressLog.from_surface(surface).read_events()
                      if e.get("type") == REVIEW_VERDICT]
            self.assertEqual(len(events), 1)
            e = events[0]
            self.assertEqual(tuple(e.keys()), ENVELOPE_KEYS)
            self.assertEqual(e["stage"], "review")
            self.assertEqual(e["run_id"], "solo")
            self.assertEqual(e["data"], {"passed": False, "independent": True, "findings": 3})

    def test_badge_path_unchanged(self):
        """The badge still resolves session-awarely AND still retires a shipped run; a payload with
        no session_id still renders the find_active_run badge."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "runA", passed=["brainstorm"])
            self.assertEqual(progress.build_stage_badge(Surface.load(d), session_id="fresh"),
                             "mokata")
            self.assertIn("›spec‹", progress.build_stage_badge(surface))
            bind_session_run(d, "sessA", "runA")
            self.assertEqual(resolve_badge_run(d, "sessA"), "runA")

    def test_progress_view_unchanged_for_a_single_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "solo", passed=PHASES[:2])
            rc, out = _cli(["progress", "--path", d], d)
            self.assertEqual(rc, 0)
            self.assertIn("[2/7 done]", out)          # the tracker still resolves the run
            self.assertIn("← you are here", out)


if __name__ == "__main__":
    unittest.main()
