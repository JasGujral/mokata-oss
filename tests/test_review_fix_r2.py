"""REVIEW-FIX.R2 — bounded-but-correct verdict read: the WINDOW, not the key (0.0.16).

R1 made the review verdict RUN-KEYED and fail-closed. This stage fixes the window those keyed
reads scan.

The bug: `state/progress-events.jsonl` is ONE shared, never-rotated log that every run's every
phase entry appends to, and every "what is the latest X?" read took `read_events()` — the trailing
`DEFAULT_TAIL` = 200 events. On a busy repo the evidence slides out of that window:

  1. a run's OWN `review_verdict` falls past 200 events ⇒ `review-status` reports "review hasn't
     run" while the verdict sits on disk. Post-R1 that fails CLOSED (a false BLOCK, not a false
     pass) — still wrong, and a gate that blocks on evidence that is right there is a gate people
     learn to route around;
  2. RETIREMENT itself gets truncated: `_shipped_run_ids` rides the same 200-tail, so a run whose
     terminal `stage_enter: ship` fell out of the window reads as UNSHIPPED and `find_active_run`
     resurrects a finished run as "the current run".

  3. and R1's filed RESIDUAL: with exactly one run in a repo the key resolves the same forever, so
     a week-old PASS still satisfied `/ship`. Nothing on disk distinguished "just reviewed" from "a
     cleared session a week later" — except the recency the log already records.

The fix under test:
  1. a run-filtered bounded BACKWARD scan (`read_events_backward` / `find_last_event`), stopping at
     the first match for the run being asked about; the byte cap is a SAFETY VALVE, not the bound
     the answer depends on;
  2. the same discipline for `_shipped_run_ids` (+ a run-filtered form for the badge's per-run
     question), so retirement cannot be truncated away;
  3. a freshness bound on the verdict READ — `settings.review.verdict_max_age_hours`, default 24 —
     so stale evidence blocks with a re-run remedy;
  4. negatives: a quiet repo behaves identically, the event shape stays backward-compatible, and
     `read_events`' signature/semantics are untouched for its remaining callers.

Business-level asserts: what `/ship` observes (the `review-status` exit code + line, the
`ReviewGate` ship reads, the badge and the progress view), never implementation poking.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import config_cmd, progress
from mokata.run_resolver import bind_session_run
from mokata.config import Surface
from mokata.govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from mokata.progress import (
    build_progress,
    STAGE_BADGE_STAGES,
    _shipped_run_ids,
    list_runs,
    list_runs_by_recency,
)
from mokata.progress_events import (
    DEFAULT_TAIL,
    ENVELOPE_KEYS,
    REVIEW_VERDICT,
    REVIEW_VERDICT_MAX_AGE_HOURS,
    SCAN_BYTE_CAP,
    STAGE_ENTER,
    ProgressLog,
    latest_review_verdict,
    latest_review_verdict_event,
    record_review_verdict,
    review_verdict_max_age_hours,
    ship_review_gate,
)
from mokata.state import StateStore
from mokata.tdd_state import state_dir

PHASES = ("brainstorm", "analysis", "strawman", "pre_mortem", "probes",
          "completeness_gate", "emit")

# Comfortably past the pre-R2 window (`DEFAULT_TAIL` = 200) — "a busy repo", in one number.
FLOOD = 300


# --------------------------------------------------------------------------- fixtures
def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _persist_run(root, run_id, passed=()):
    cp = PipelineCheckpoint(Surface.load(root).state, run_id)
    cp.ensure_registered()
    for p in passed:
        cp.mark_passed(p)
    return cp


def _flood(surface, n=FLOOD, run_id="other", stage="develop"):
    """`n` more phase entries land in the SHARED log — exactly what `mokata progress mark` writes
    every time any command in any window enters a stage."""
    log = ProgressLog.from_surface(surface)
    for _ in range(n):
        log.append_event(STAGE_ENTER, stage, run_id=run_id)


def _mark_ship(surface, run_id):
    """Retire `run_id` EXACTLY as `mokata progress mark ship` does (cmd_progress_mark)."""
    ProgressLog.from_surface(surface).append_event(STAGE_ENTER, "ship", run_id=run_id)
    PipelineCheckpoint(surface.state, run_id).mark_completed()


def _rewrite_verdicts(surface, *, hours_ago=None, drop_ts=False):
    """Age (or de-stamp) the `review_verdict` events already on disk — the only way to build "a
    verdict recorded a week ago" without waiting a week. Every other line is passed through
    byte-for-byte."""
    path = ProgressLog.from_surface(surface).path
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    out = []
    for ln in lines:
        e = json.loads(ln)
        if e.get("type") == REVIEW_VERDICT:
            if drop_ts:
                e.pop("ts", None)
            else:
                when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
                e["ts"] = when.isoformat()
        out.append(json.dumps(e))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def _set_hours(root, value):
    """Set the bound through the REAL gated config path (`mokata config set`)."""
    res = config_cmd.config_set(root, "settings.review.verdict_max_age_hours", value,
                                assume_yes=True, out=lambda _: None)
    assert res.committed, res.message


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


# ------------------------------------------------- the PRE-R2 reads, reconstructed verbatim
def _old_verdict_read(surface, run_id):
    """The pre-R2 verdict read, verbatim — `progress_events.py:213` + its loop:

        events = ProgressLog.from_surface(surface).read_events()   # <- the last DEFAULT_TAIL events
        for e in events: ... if e.get("run_id") != run_id: continue

    The window is the whole bug: nothing here is wrong except WHERE it looked."""
    found = None
    for e in ProgressLog.from_surface(surface).read_events():
        if e.get("type") != REVIEW_VERDICT:
            continue
        if e.get("run_id") != run_id:
            continue
        data = e.get("data")
        if isinstance(data, dict):
            found = data
    return found


def _old_shipped_run_ids(store):
    """The pre-R2 `progress._shipped_run_ids`, verbatim (progress.py:118-130) — same 200-tail."""
    events = ProgressLog.from_state_dir(store.root).read_events()
    furthest = {}
    for e in events:
        if e.get("type") != STAGE_ENTER:
            continue
        stage = e.get("stage")
        if stage in STAGE_BADGE_STAGES:
            furthest[e.get("run_id")] = stage
    return {rid for rid, s in furthest.items() if s == "ship"}


def _old_find_active_run(store):
    """`find_active_run` as it behaved with the truncated retirement set above."""
    for rid in list_runs(store):
        if not PipelineCheckpoint(store, rid).is_complete(PHASES):
            return rid
    shipped = _old_shipped_run_ids(store)
    for rid in reversed(list_runs_by_recency(store)):
        if rid not in shipped:
            return rid
    return None


class _NoPinnedSession(unittest.TestCase):
    """`MOKATA_SESSION_ID` pins run resolution (gate_hook.resolve_run) — a pin leaking in from the
    environment would silently change what these scenarios resolve to."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("MOKATA_SESSION_ID", None)


# ======================================================= THE regression (pins both truncations)
class TestReviewFixR2Regression(_NoPinnedSession):
    def test_review_fix_r2_regression(self):
        """Both halves of the truncation, each pinned against a verbatim reconstruction of the
        code this stage replaced.

        (a) VERDICT — one run, review recorded, then `FLOOD` more phase entries. The old read saw
            only the trailing 200 events, so the run's own verdict vanished and `/ship` was told
            "review hasn't run" (a false BLOCK). The new read finds it.
        (b) RETIREMENT — a SHIPPED run, then `FLOOD` events from another window's run. The old
            read lost the terminal `ship` event, so the finished run read as unshipped and
            `find_active_run` resurrected it as the current run. The new read keeps it retired."""
        # (a) the verdict
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            self.assertIsNotNone(_old_verdict_read(surface, "solo"))     # quiet repo: old was fine
            _flood(surface, run_id="solo")

            # PIN THE BUG — the evidence is on disk, and the old window cannot see it.
            self.assertIsNone(_old_verdict_read(surface, "solo"),
                              "pre-R2 read lost the verdict past the 200-event tail")
            rc_old = 2 if _old_verdict_read(surface, "solo") is None else 0
            self.assertEqual(rc_old, 2, "pre-R2 /ship was blocked by its own noise")

            # NEW — found, and ship proceeds.
            self.assertEqual(latest_review_verdict(surface, run_id="solo"),
                             {"passed": True, "independent": True})
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 0, out)
            self.assertIn("review passed (independent ✓)", out)

        # (b) retirement
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "shipped", passed=PHASES)
            _mark_ship(surface, "shipped")
            self.assertEqual(_old_shipped_run_ids(surface.state), {"shipped"})   # quiet: fine
            _flood(surface, run_id="another-window")

            # PIN THE BUG — retirement truncated away, so a finished run comes back as current.
            self.assertEqual(_old_shipped_run_ids(surface.state), set())
            self.assertEqual(_old_find_active_run(surface.state), "shipped",
                             "pre-R2 code resurrected a shipped run")

            # NEW — still retired; no run is reported as active, and the badge is clean.
            self.assertEqual(_shipped_run_ids(surface.state), {"shipped"})
            # RUN-ID-DRIFT — the retirement now narrows inside the ONE resolver (rung viii) and is
            # applied again as display policy by `build_progress`; either way the shipped run is
            # not reported as current.
            self.assertIsNone(build_progress(surface.state, root=d).run_id)
            bind_session_run(d, "sessA", "shipped")
            self.assertEqual(progress.build_stage_badge(Surface.load(d), session_id="sessA"),
                             "mokata")


# ======================================================= 1 · the bounded backward verdict read
class TestBoundedBackwardRead(_NoPinnedSession):
    def test_verdict_survives_a_flooded_log(self):
        """The deliverable, at the surface ship actually reads: `review-status` still finds this
        run's verdict with `FLOOD` events piled on top of it."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            _flood(surface, run_id="solo")
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 0, out)
            self.assertIn("review passed (independent ✓)", out)
            self.assertFalse(ship_review_gate(surface, run_id="solo").blocks)

    def test_a_failed_verdict_also_survives_the_flood(self):
        """The window fix is polarity-blind — a FAILED review must not become invisible either
        (that would be the false PASS the fail-closed design exists to prevent)."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=False, independent=True)
            _flood(surface, run_id="solo")
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 2)
            self.assertIn("review failed", out)

    def test_the_scan_stops_at_the_first_match_and_does_not_walk_the_history(self):
        """Bounded by CONSTRUCTION, not by the cap: the read stops at this run's own verdict, so
        the work it does is set by how recent the evidence is, not by how long the log is."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            _flood(surface, n=1000, run_id="solo")            # deep history BEFORE the verdict
            record_review_verdict(surface, passed=True, independent=True)
            _flood(surface, n=5, run_id="solo")               # a little noise after it

            seen = []
            log = ProgressLog.from_surface(surface)

            def _match(e):
                seen.append(e)
                return e.get("type") == REVIEW_VERDICT and e.get("run_id") == "solo"

            self.assertIsNotNone(log.find_last_event(_match))
            self.assertLessEqual(len(seen), 10,
                                 "the scan walked past the match it was looking for")

    def test_the_byte_cap_is_a_safety_valve_not_the_answers_bound(self):
        """The cap exists, it is real, and it is nowhere near the answer. With the SHIPPED cap a
        verdict buried under a flood is found; with an absurd 512-byte cap the scan gives up —
        which is what "hitting it means something is wrong" looks like, and it fails CLOSED."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            _flood(surface, run_id="solo")
            log = ProgressLog.from_surface(surface)
            match = (lambda e: e.get("type") == REVIEW_VERDICT)

            self.assertIsNotNone(log.find_last_event(match))                  # shipped cap: found
            self.assertIsNone(log.find_last_event(match, byte_cap=512))       # valve: gives up
            self.assertGreater(SCAN_BYTE_CAP, 1_000_000)
            # ...and the shipped cap is orders of magnitude clear of a real repo's log.
            self.assertGreater(SCAN_BYTE_CAP, os.path.getsize(log.path) * 100)

    def test_backward_scan_reads_the_same_events_as_the_forward_read(self):
        """Same file, same events, opposite order — a torn last line and a missing file included."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            log = ProgressLog.from_surface(surface)
            self.assertEqual(list(log.read_events_backward()), [])            # absent file
            for i in range(50):
                log.append_event(STAGE_ENTER, "develop", run_id="r", data={"n": i})
            self.assertEqual(list(log.read_events_backward()),
                             list(reversed(log.read_events(tail=0))))
            with open(log.path, "a", encoding="utf-8") as fh:
                fh.write('{"event_id": "torn", "ts": ')                       # half-written line
            self.assertEqual([e["data"]["n"] for e in log.read_events_backward()],
                             list(reversed(range(50))))


# ======================================================= 2 · retirement on the same discipline
class TestRetirementCannotBeTruncated(_NoPinnedSession):
    def test_a_shipped_run_stays_shipped_under_a_flooded_log(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "shipped", passed=PHASES)
            _mark_ship(surface, "shipped")
            _flood(surface, run_id="another-window")
            self.assertEqual(_shipped_run_ids(surface.state), {"shipped"})
            self.assertEqual(_shipped_run_ids(surface.state, "shipped"), {"shipped"})

    def test_the_badge_still_retires_a_flooded_out_shipped_run(self):
        """`run_resolver._run_is_shipped` asks the run-filtered question now; the answer must not
        depend on how much noise landed after the ship event."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _persist_run(d, "shipped", passed=PHASES)
            _mark_ship(Surface.load(d), "shipped")
            _flood(Surface.load(d), run_id="another-window")
            bind_session_run(d, "sessA", "shipped")
            self.assertEqual(progress.build_stage_badge(Surface.load(d), session_id="sessA"),
                             "mokata")

    def test_a_run_that_resumed_after_shipping_is_active_again(self):
        """The B-LIFE semantic is unchanged by the new direction of travel: the LAST logged stage
        wins, and walking backward the first one seen IS the last one."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "runA", passed=PHASES)
            _mark_ship(surface, "runA")
            ProgressLog.from_surface(surface).append_event(STAGE_ENTER, "develop", run_id="runA")
            _flood(surface, run_id="other")
            self.assertEqual(_shipped_run_ids(surface.state), set())
            self.assertEqual(build_progress(surface.state, root=d).run_id, "runA")

    def test_run_filtered_scan_ignores_other_runs_ship_events(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "mine", passed=PHASES)
            _persist_run(d, "theirs", passed=PHASES)
            _mark_ship(surface, "theirs")
            self.assertEqual(_shipped_run_ids(surface.state, "mine"), set())
            self.assertEqual(_shipped_run_ids(surface.state, "theirs"), {"theirs"})


# ======================================================= 3 · the freshness bound (R1's residual)
class TestVerdictFreshness(_NoPinnedSession):
    def test_a_fresh_verdict_is_unaffected(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 0, out)
            self.assertIn("review passed (independent ✓)", out)

    def test_a_verdict_just_inside_the_bound_still_ships(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            _rewrite_verdicts(surface, hours_ago=REVIEW_VERDICT_MAX_AGE_HOURS - 1)
            self.assertFalse(ship_review_gate(surface, run_id="solo").blocks)

    def test_an_aged_out_verdict_blocks_with_the_rerun_remedy(self):
        """R1's residual, closed: the single-run repo whose week-old PASS satisfied `/ship`."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            _rewrite_verdicts(surface, hours_ago=24 * 7)

            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual(rc, 2, out)
            self.assertIn("review evidence is stale (recorded ", out)
            self.assertIn("re-run /mokata:review", out)
            gate = ship_review_gate(surface, run_id="solo")
            self.assertTrue(gate.blocks)
            self.assertTrue(gate.present)          # the verdict exists...
            self.assertFalse(gate.passed)          # ...but it does not satisfy the gate
            self.assertIn("verdict_max_age_hours", gate.unblock)

    def test_a_stale_FAILED_verdict_keeps_the_more_specific_message(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=False, independent=True)
            _rewrite_verdicts(surface, hours_ago=24 * 7)
            gate = ship_review_gate(surface, run_id="solo")
            self.assertTrue(gate.blocks)
            self.assertIn("review failed", gate.message)

    def test_the_bound_is_configurable(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            _rewrite_verdicts(surface, hours_ago=48)                   # 2 days old

            self.assertTrue(ship_review_gate(Surface.load(d), run_id="solo").blocks)  # default 24h
            _set_hours(d, "168")                                       # a week's leash
            self.assertEqual(review_verdict_max_age_hours(Surface.load(d)), 168.0)
            self.assertFalse(ship_review_gate(Surface.load(d), run_id="solo").blocks)
            _set_hours(d, "1")                                         # a tight leash
            self.assertTrue(ship_review_gate(Surface.load(d), run_id="solo").blocks)

    def test_the_bound_can_be_switched_off(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            _rewrite_verdicts(surface, hours_ago=24 * 365)
            for off in ('"off"', "0"):
                _set_hours(d, off)
                self.assertEqual(review_verdict_max_age_hours(Surface.load(d)), 0.0)
                self.assertFalse(ship_review_gate(Surface.load(d), run_id="solo").blocks)

    def test_a_nonsense_bound_keeps_the_default_rather_than_disabling_the_check(self):
        """Degrade in the SAFE direction: a broken setting must never silently switch the freshness
        check off (`0`/`off` is the only way to do that, and it is explicit)."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            _rewrite_verdicts(surface, hours_ago=24 * 7)
            for junk in ('"soon"', "true", "null", "-5"):
                _set_hours(d, junk)
                loaded = Surface.load(d)
                if junk == "-5":
                    self.assertEqual(review_verdict_max_age_hours(loaded), 0.0)   # explicit ≤0
                    continue
                self.assertEqual(review_verdict_max_age_hours(loaded),
                                 REVIEW_VERDICT_MAX_AGE_HOURS)
                self.assertTrue(ship_review_gate(loaded, run_id="solo").blocks)

    def test_a_verdict_with_no_timestamp_is_treated_as_stale(self):
        """Degrade-clean, and the direction is grounded: `append_event` has stamped `ts` on EVERY
        entry since Stage 6b (it is a pinned `ENVELOPE_KEYS` member), so a stamp-less verdict is
        not a legacy shape mokata ever wrote — it is a hand-edited or torn record. On a fail-closed
        ship gate, unreadable evidence is not evidence, and the remedy is one command."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            _rewrite_verdicts(surface, drop_ts=True)
            gate = ship_review_gate(surface, run_id="solo")
            self.assertTrue(gate.blocks)
            self.assertIn("recorded at an unknown time", gate.message)
            # ...and it is the BOUND doing it: switch the bound off and the verdict is honoured.
            _set_hours(d, '"off"')
            self.assertFalse(ship_review_gate(Surface.load(d), run_id="solo").blocks)

    def test_a_future_timestamp_reads_as_fresh(self):
        """Clock skew / an imported log: mokata refuses STALE evidence, it does not police clocks."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            _rewrite_verdicts(surface, hours_ago=-48)
            self.assertFalse(ship_review_gate(surface, run_id="solo").blocks)

    def test_freshness_is_read_from_the_runs_own_verdict_not_the_newest_one(self):
        """The bound rides the KEYED read (R1), so another run's fresh review cannot refresh mine."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "mine", passed=PHASES)
            _persist_run(d, "theirs", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True, run_id="mine")
            _rewrite_verdicts(surface, hours_ago=24 * 7)
            record_review_verdict(surface, passed=True, independent=True, run_id="theirs")
            self.assertTrue(ship_review_gate(surface, run_id="mine").blocks)
            self.assertFalse(ship_review_gate(surface, run_id="theirs").blocks)


# ======================================================= 4 · the negatives
class TestNoBehaviourChange(_NoPinnedSession):
    def test_quiet_repo_behaviour_is_byte_identical(self):
        """One run, one review, no noise — the resolved key, the recorded event, the gate line and
        the CLI exit code are exactly what R1 left behind."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            event = record_review_verdict(surface, passed=True, independent=True)
            self.assertEqual(event["run_id"], "solo")
            self.assertEqual(event["data"], {"passed": True, "independent": True})
            self.assertEqual(latest_review_verdict(surface, run_id="solo"),
                             {"passed": True, "independent": True})
            gate = ship_review_gate(surface)
            self.assertEqual(gate.message, "review passed (independent ✓)")
            self.assertFalse(gate.blocks)
            self.assertTrue(gate.present and gate.passed and gate.independent)
            rc, out = _cli(["progress", "review-status", "--path", d], d)
            self.assertEqual((rc, out.strip()), (0, "review passed (independent ✓)"))

    def test_r1s_refusals_are_untouched(self):
        """A run-less read still REFUSES and a run-less repo still BLOCKS with R1's remedy — the
        window fix must not have reopened either door."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            record_review_verdict(surface, passed=True, independent=True, run_id=None)
            _flood(surface, run_id=None)
            self.assertIsNone(latest_review_verdict(surface, run_id=None))
            self.assertIsNone(latest_review_verdict_event(surface, run_id=None))
            gate = ship_review_gate(surface)
            self.assertTrue(gate.blocks)
            self.assertIn("no run to attribute it to", gate.message)

    def test_a_malformed_surface_still_degrades_to_a_block(self):
        class Broken:
            pass
        self.assertIsNone(latest_review_verdict(Broken(), run_id="run-a"))
        self.assertIsNone(latest_review_verdict_event(Broken(), run_id="run-a"))
        self.assertTrue(ship_review_gate(Broken(), run_id="run-a").blocks)

    def test_event_shape_is_backward_compatible(self):
        """No schema change: the envelope keys, their order, the `data` payload and the log's
        one-JSON-object-per-line format are unchanged, so an OLD reader of the same file (the
        forward `read_events`) still parses every entry."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=False, independent=True, findings=3)
            log = ProgressLog.from_surface(surface)
            events = [e for e in log.read_events(tail=0) if e.get("type") == REVIEW_VERDICT]
            self.assertEqual(len(events), 1)
            e = events[0]
            self.assertEqual(tuple(e.keys()), ENVELOPE_KEYS)
            self.assertEqual(e["stage"], "review")
            self.assertEqual(e["run_id"], "solo")
            self.assertEqual(e["data"], {"passed": False, "independent": True, "findings": 3})
            with open(log.path, encoding="utf-8") as fh:
                for line in fh:
                    self.assertIsInstance(json.loads(line), dict)

    def test_read_events_signature_and_semantics_unchanged(self):
        """`read_events` keeps its public contract for the callers that still hold it — the badge's
        `progress._logged_user_stage` and every test/reader outside this stage. The callers that
        moved: `progress_events.latest_review_verdict*` and `progress._shipped_run_ids`."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            log = ProgressLog.from_surface(surface)
            for i in range(5):
                log.append_event(STAGE_ENTER, "develop", run_id="r", data={"n": i})
            self.assertEqual(len(log.read_events()), 5)                  # default tail
            self.assertEqual([e["data"]["n"] for e in log.read_events(tail=3)], [2, 3, 4])
            self.assertEqual(len(log.read_events(tail=0)), 5)            # 0 -> everything
            self.assertEqual(len(log.read_events(tail=None)), 5)
            self.assertEqual(DEFAULT_TAIL, 200)                          # constant unchanged

    def test_badge_and_progress_view_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES[:2])
            self.assertIn("›spec‹", progress.build_stage_badge(surface))
            self.assertEqual(progress.build_stage_badge(Surface.load(d), session_id="fresh"),
                             "mokata")
            rc, out = _cli(["progress", "--path", d], d)
            self.assertEqual(rc, 0)
            self.assertIn("[2/7 done]", out)
            self.assertIn("← you are here", out)

    def test_the_verdict_record_path_is_unchanged(self):
        """R2 touched the READ. Recording still stamps the same envelope and needs no new field —
        the timestamp the freshness bound reads was already there."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            before = datetime.now(timezone.utc)
            event = record_review_verdict(surface, passed=True, independent=True)
            self.assertIn("ts", event)
            self.assertGreaterEqual(datetime.fromisoformat(event["ts"]), before)
            self.assertEqual(set(event["data"]), {"passed", "independent"})

    def test_state_store_and_ledger_are_untouched_by_a_read(self):
        """The gate is a READ: reading it (however often) writes nothing."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _persist_run(d, "solo", passed=PHASES)
            record_review_verdict(surface, passed=True, independent=True)
            path = ProgressLog.from_surface(surface).path
            before = (os.path.getsize(path), sorted(os.listdir(state_dir(d))))
            for _ in range(3):
                ship_review_gate(surface, run_id="solo")
            self.assertEqual((os.path.getsize(path), sorted(os.listdir(state_dir(d)))), before)
            self.assertTrue(os.path.exists(os.path.join(state_dir(d),
                                                        CHECKPOINT_PREFIX + "solo.json")))
            self.assertEqual(list_runs(StateStore(state_dir(d))), ["solo"])


if __name__ == "__main__":
    unittest.main()
