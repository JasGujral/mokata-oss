"""Stage 6c — the develop sub-counter in the always-on stage badge.

When the badge is at `develop` AND a real decomposition/execmode batch exists for the run, the
badge shows a REAL `develop … <done>/<total>` task counter — the develop analogue of the spec
`[n/m]` phase counter, same honesty rule. This proves:

  * a run at develop with a real fan-out batch shows the derived done/total;
  * NO batch on record -> NO counter (exactly like review/ship render none today), never `0/0`;
  * a sequential run (no fan-out) shows NO counter (not `0/0` or any invented number);
  * the counter is DERIVED from the SINGLE build_run_lanes source — changing the ledger's per-task
    state changes ONLY the counter, never the stage arc (single-source, zero independent tracking);
  * the spec-stage phase counter is unchanged; everything degrades clean.
"""

import os
import re
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import progress as P
from mokata.config import Surface
from mokata.govern import AuditLedger
from mokata.govern.resume import PipelineCheckpoint


def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _at_develop(surface, rid="run-a"):
    """Push a run's checkpoint to complete (spec emitted) so the badge sits at `develop`."""
    cp = PipelineCheckpoint(surface.state, rid)
    for ph in P.PIPELINE_PHASES:
        cp.mark_passed(ph)
    return rid


def _mid_spec(surface, rid="run-s"):
    """A run partway through the spec phases (not brainstorm, not complete) — badge at `spec`."""
    cp = PipelineCheckpoint(surface.state, rid)
    cp.mark_passed("brainstorm")
    cp.mark_passed("analysis")
    return rid


def _parallel_batch(surface, *, tasks, done):
    """A parallel exec batch of `tasks` total with `done` subagents already passed (the rest
    are still mid-flight, padded by build_run_lanes)."""
    led = AuditLedger.from_mokata_dir(surface.mokata_dir)
    led.record("exec_estimate", mode="parallel", tasks=tasks)
    for i in range(done):
        led.record("subagent", task=f"t{i + 1}", ok=True, review_passed=True)
    return led


def _sequential_batch(surface, *, tasks=2):
    led = AuditLedger.from_mokata_dir(surface.mokata_dir)
    led.record("exec_estimate", mode="sequential", tasks=tasks)
    led.record("sequential", task="t1", ok=True)
    return led


def _stage_strip(badge):
    """The `[brainstorm · … · ship]` arc of a badge — the part that must NOT move with task state."""
    m = re.search(r"\[.*\]", badge)
    return m.group(0) if m else ""


# ================================================================= a real batch -> real counter
class TestDevelopCounterFromRealBatch(unittest.TestCase):
    def test_badge_shows_develop_done_over_total(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)
            _parallel_batch(s, tasks=3, done=2)          # 2 of 3 tasks done
            stage, counter = P._badge_state(s)
            self.assertEqual(stage, "develop")
            self.assertEqual(counter, "2/3")             # DERIVED done/total, not fabricated
            badge = P.build_stage_badge(s)
            self.assertIn("›develop‹", badge)            # the active stage is develop
            self.assertIn(" · 2/3", badge)               # rendered like the spec [n/m] counter

    def test_counter_matches_build_run_lanes_single_source(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)
            led = _parallel_batch(s, tasks=4, done=1)
            rl = P.build_run_lanes(s.state, ledger=led)  # THE single source
            self.assertEqual(P.develop_counter(rl), "1/4")
            self.assertEqual(P._badge_state(s)[1], "1/4")


# ================================================================= no batch / sequential -> none
class TestNoCounterWithoutARealBatch(unittest.TestCase):
    def test_no_batch_on_record_means_no_counter(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)                               # at develop, but NO execmode batch
            stage, counter = P._badge_state(s)
            self.assertEqual(stage, "develop")
            self.assertEqual(counter, "")                # like review/ship: no counter at all
            badge = P.build_stage_badge(s)
            self.assertIn("›develop‹", badge)
            self.assertNotIn("/", badge.split("›develop‹", 1)[1])   # no n/m anywhere after it

    def test_sequential_run_shows_no_counter_not_zero_zero(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)
            _sequential_batch(s, tasks=2)                # a real batch, but NOT a fan-out
            stage, counter = P._badge_state(s)
            self.assertEqual(stage, "develop")
            self.assertEqual(counter, "")                # never a fabricated 0/0 for a serial run
            self.assertNotIn("0/0", P.build_stage_badge(s))


# ================================================================= single source: only the counter
class TestSingleSourceOnlyTheCounterMoves(unittest.TestCase):
    def test_changing_ledger_task_state_changes_only_the_counter(self):
        """Two identical develop runs differing ONLY in how many tasks the ledger records done:
        the counter tracks the ledger (proving it is DERIVED, not independently tracked), while
        the badge's stage arc is byte-identical (nothing else in the badge moves)."""
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            s1, s2 = _repo(d1), _repo(d2)
            _at_develop(s1); _at_develop(s2)
            _parallel_batch(s1, tasks=3, done=1)         # same batch shape,
            _parallel_batch(s2, tasks=3, done=2)         # only the on-disk done-count differs

            c1, c2 = P._badge_state(s1)[1], P._badge_state(s2)[1]
            self.assertEqual((c1, c2), ("1/3", "2/3"))   # the ledger state drives the counter
            # the stage arc (everything that ISN'T the counter) is unchanged by task state:
            self.assertEqual(_stage_strip(P.build_stage_badge(s1)),
                             _stage_strip(P.build_stage_badge(s2)))

    def test_appending_a_done_task_advances_only_the_counter(self):
        """Append one more done subagent to the SAME ledger — the counter advances, the stage
        arc does not (the counter reads through to the ledger, a single live source)."""
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _at_develop(s)
            led = _parallel_batch(s, tasks=3, done=1)
            before = P.build_stage_badge(s)
            self.assertEqual(P._badge_state(s)[1], "1/3")
            led.record("subagent", task="t2", ok=True, review_passed=True)   # a task finishes
            after = P.build_stage_badge(s)
            self.assertEqual(P._badge_state(s)[1], "2/3")                     # counter moved
            self.assertEqual(_stage_strip(before), _stage_strip(after))      # arc did not


# ================================================================= spec counter unchanged + degrade
class TestSpecCounterUntouchedAndDegradeClean(unittest.TestCase):
    def test_spec_stage_phase_counter_is_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            s = _repo(d)
            _mid_spec(s)                                 # badge at spec, not develop
            stage, counter = P._badge_state(s)
            self.assertEqual(stage, "spec")
            self.assertEqual(counter, "2/7")            # the pipeline phase fraction, as before
            self.assertIn(" · 2/7", P.build_stage_badge(s))

    def test_develop_counter_helper_degrades_clean(self):
        # None / inactive / non-fanout lane views -> "" (never raises, never fabricates).
        self.assertEqual(P.develop_counter(None), "")
        self.assertEqual(P.develop_counter(P.RunLanes(active=False, mode="none")), "")
        self.assertEqual(
            P.develop_counter(P.RunLanes(active=True, mode="sequential", tasks=3)), "")
        # a parallel view with no task total -> "" (no fabricated count)
        self.assertEqual(
            P.develop_counter(P.RunLanes(active=True, mode="parallel", tasks=0)), "")

    def test_unreadable_surface_yields_no_counter_not_an_error(self):
        class Broken:
            mokata_dir = "/nonexistent/mokata"
            @property
            def state(self):
                raise RuntimeError("unreadable")
        self.assertEqual(P._develop_counter(Broken()), "")   # degrade-clean, no raise


if __name__ == "__main__":
    unittest.main()
