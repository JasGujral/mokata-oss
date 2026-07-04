"""Stage 6b — minimal, event-stream-shaped pipeline progress LOG.

An append-only, UNGATED (observability-tier) JSONL log of user-stage transitions, so the
always-on badge (Stage 54b) can tell develop / review / ship apart — three separate skills
that share no pipeline checkpoint (the old "spec emitted -> always develop" collapse).

These tests assert:
  * the log mirrors the audit ledger: append-only JSONL, bounded tail read, degrade-clean
    (absent / corrupt / unreadable -> empty, never raises);
  * the event ENVELOPE is a COMPATIBLE SUBSET of 0.1.0's R1.S1a event stream (so R1.S1a can
    absorb it as a superset, not a second log);
  * the badge ADVANCES from the LOG across brainstorm -> spec -> develop -> review -> ship,
    marking prior stages done (✓) — derived from the log, not the checkpoint alone;
  * a corrupt / absent log degrades to the checkpoint-derived default;
  * writing an event does NOT trip the WriteGate / human-gate (observability, like the
    ledger — proven by an unchanged audit ledger);
  * the `mokata progress mark <stage>` writer appends a stage_enter;
  * develop / review / ship templates carry the entry-recording instruction (single source).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import progress
from mokata.config import Surface
from mokata.govern import AuditLedger, PipelineCheckpoint
from mokata.progress_events import (
    ENVELOPE_KEYS,
    PROGRESS_EVENT_TYPES,
    PROGRESS_SCHEMA_VERSION,
    REVIEW_VERDICT,
    STAGE_ENTER,
    ProgressLog,
)

PHASES = ("brainstorm", "analysis", "strawman", "pre_mortem", "probes",
          "completeness_gate", "emit")


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _complete_run(surface, run_id="run-a"):
    """Drive a pipeline run to completion (spec emitted) — badge collapses to develop."""
    cp = PipelineCheckpoint(surface.state, run_id)
    for p in PHASES:
        cp.mark_passed(p)
    return cp


# ============================================================ the log (ledger-shaped)
class TestProgressLog(unittest.TestCase):
    def test_append_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            log = ProgressLog.from_surface(surface)
            e = log.append_event(STAGE_ENTER, "develop", run_id="run-a")
            events = log.read_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["stage"], "develop")
            self.assertEqual(events[0]["run_id"], "run-a")
            self.assertEqual(events[0]["type"], STAGE_ENTER)
            self.assertEqual(events[0]["event_id"], e["event_id"])

    def test_absent_log_reads_empty(self):
        with tempfile.TemporaryDirectory() as d:
            log = ProgressLog.from_state_dir(os.path.join(d, "nope"))
            self.assertEqual(log.read_events(), [])          # never raises

    def test_corrupt_lines_skipped_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            log = ProgressLog.from_surface(surface)
            log.append_event(STAGE_ENTER, "develop")
            # append a torn / non-JSON line, as a half-written flush might leave.
            with open(log.path, "a", encoding="utf-8") as fh:
                fh.write("{not valid json\n")
                fh.write("\n")                               # blank line
            log.append_event(STAGE_ENTER, "review")
            events = log.read_events()
            self.assertEqual([e["stage"] for e in events], ["develop", "review"])

    def test_append_only_and_bounded_tail(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            log = ProgressLog.from_surface(surface)
            for i in range(10):
                log.append_event(STAGE_ENTER, "develop", data={"n": i})
            # every line is still on disk (append-only — nothing rewritten)
            with open(log.path, encoding="utf-8") as fh:
                self.assertEqual(sum(1 for ln in fh if ln.strip()), 10)
            # a bounded read returns only the tail
            tail = log.read_events(tail=3)
            self.assertEqual([e["data"]["n"] for e in tail], [7, 8, 9])

    def test_envelope_is_r1s1a_compatible_subset(self):
        # R1.S1a's envelope (42-mokata-0.1.x-plan.md, R1.S1a) is
        # {event_id, session_id, actor, ts, schema_version} + a typed `type`/payload. The
        # 6b entry must be a COMPATIBLE SUBSET: the shared envelope fields present + named
        # identically, `run_id` the forward hook for `session_id`, `type` one of the known
        # (PhaseTransition-mapping) kinds, `data` an extensible payload. Then R1.S1a ingests
        # each 6b entry without a translation layer.
        r1s1a_shared = {"event_id", "ts", "schema_version"}
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            log = ProgressLog.from_surface(surface)
            log.append_event(STAGE_ENTER, "develop", run_id="run-a")
            log.append_event(REVIEW_VERDICT, "review", run_id="run-a",
                             data={"passed": True, "independent": False})
            for e in log.read_events():
                self.assertEqual(set(e.keys()), set(ENVELOPE_KEYS))     # exactly the envelope
                self.assertTrue(r1s1a_shared.issubset(e.keys()))       # shares R1.S1a's core
                self.assertIn(e["type"], PROGRESS_EVENT_TYPES)         # a known event type
                self.assertEqual(e["schema_version"], PROGRESS_SCHEMA_VERSION)
                self.assertIsInstance(e["data"], dict)                 # extensible payload
                # ts is UTC ISO-8601 (parses, carries an offset)
                self.assertIn("+00:00", e["ts"])


# ============================================================ the badge reads the log
class TestBadgeFromLog(unittest.TestCase):
    def test_badge_advances_develop_review_ship_from_log(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _complete_run(surface)                        # spec emitted -> develop
            log = ProgressLog.from_surface(surface)
            done = progress._GLYPHS[progress.DONE]

            log.append_event(STAGE_ENTER, "develop", run_id="run-a")
            self.assertIn("›develop‹", progress.build_stage_badge(surface))

            log.append_event(STAGE_ENTER, "review", run_id="run-a")
            b = progress.build_stage_badge(surface)
            self.assertIn("›review‹", b)
            self.assertIn(f"{done}develop", b)            # prior stage now done ✓

            log.append_event(STAGE_ENTER, "ship", run_id="run-a")
            b = progress.build_stage_badge(surface)
            self.assertIn("›ship‹", b)
            self.assertIn(f"{done}brainstorm", b)
            self.assertIn(f"{done}spec", b)
            self.assertIn(f"{done}develop", b)
            self.assertIn(f"{done}review", b)

    def test_badge_active_from_log_even_without_pipeline_run(self):
        # Zero fabrication cuts BOTH ways: if the log recorded the user entered a stage,
        # the badge shows it even with no pipeline checkpoint (the user really ran it).
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            ProgressLog.from_surface(surface).append_event(STAGE_ENTER, "review")
            self.assertIn("›review‹", progress.build_stage_badge(surface))

    def test_absent_log_falls_back_to_checkpoint_default(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _complete_run(surface)                        # checkpoint says develop
            # no log at all -> today's behaviour (develop), never an error
            self.assertIn("›develop‹", progress.build_stage_badge(surface))

    def test_corrupt_log_degrades_to_checkpoint_default(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _complete_run(surface)
            log = ProgressLog.from_surface(surface)
            os.makedirs(os.path.dirname(log.path), exist_ok=True)
            with open(log.path, "w", encoding="utf-8") as fh:
                fh.write("totally corrupt\nnot json either\n")
            # a corrupt log reads empty -> checkpoint-derived default, no traceback
            self.assertIn("›develop‹", progress.build_stage_badge(surface))

    def test_log_only_advances_forward_never_backward(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _complete_run(surface)                        # develop
            # a stray brainstorm event must NOT drag a develop-stage badge backward
            ProgressLog.from_surface(surface).append_event(
                STAGE_ENTER, "brainstorm", run_id="run-a")
            self.assertIn("›develop‹", progress.build_stage_badge(surface))

    def test_prior_runs_ship_event_does_not_shadow_a_new_run(self):
        # run_id scoping: a completed run's ship event must not leak into a fresh run's
        # brainstorm/spec (R1.S1a will make this a full session_id; run_id is enough now).
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _complete_run(surface, "run-a")               # run-a complete
            ProgressLog.from_surface(surface).append_event(
                STAGE_ENTER, "ship", run_id="run-a")
            # a NEW, still-incomplete run-b is now the active run (first incomplete)
            PipelineCheckpoint(surface.state, "run-b").mark_passed("brainstorm")
            b = progress.build_stage_badge(surface)
            self.assertIn("›spec‹", b)                    # run-b's real stage
            self.assertNotIn("›ship‹", b)                 # run-a's stale event scoped out

    def test_ascii_mode_mirrors_log_derived_badge(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _complete_run(surface)
            ProgressLog.from_surface(surface).append_event(
                STAGE_ENTER, "review", run_id="run-a")
            b = progress.build_stage_badge(surface, ascii_only=True)
            self.assertIn(">review<", b)
            self.assertIn("[x]develop", b)
            self.assertNotIn("›", b)


# ============================================================ ungated (observability)
class TestUngatedObservability(unittest.TestCase):
    def test_append_does_not_trip_the_write_gate(self):
        # The append is observability, like the audit ledger — it never routes through the
        # WriteGate/human-gate. Proof: the audit ledger records NOTHING for a progress
        # append (a gated write would log a `write_gate` decision there).
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
            before = len(ledger.entries())
            ProgressLog.from_surface(surface).append_event(STAGE_ENTER, "develop")
            after = AuditLedger.from_mokata_dir(surface.mokata_dir).entries()
            self.assertEqual(len(after), before)                   # ledger untouched
            self.assertFalse(any(e.get("kind") == "write_gate" for e in after))


# ============================================================ the CLI writer
class TestProgressMarkCLI(unittest.TestCase):
    def _run(self, argv, cwd):
        import contextlib
        import io
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

    def test_mark_appends_stage_enter(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            rc, out = self._run(["progress", "mark", "develop"], d)
            self.assertEqual(rc, 0)
            events = ProgressLog.from_surface(surface).read_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], STAGE_ENTER)
            self.assertEqual(events[0]["stage"], "develop")

    def test_mark_rejects_unknown_stage(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with self.assertRaises(SystemExit):            # argparse choices reject it
                self._run(["progress", "mark", "not-a-stage"], d)

    def test_bare_progress_still_shows_tracker(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, out = self._run(["progress"], d)
            self.assertEqual(rc, 0)
            self.assertIn("mokata", out)                   # default tracker unbroken


# ============================================================ single-source templates
class TestTemplatesRecordEntry(unittest.TestCase):
    _TDIR = Path(__file__).resolve().parents[1] / "src" / "mokata" / "templates" / "commands"

    def test_develop_review_ship_carry_the_mark_instruction(self):
        for name in ("develop", "review", "ship"):
            text = (self._TDIR / f"{name}.md").read_text(encoding="utf-8")
            self.assertIn("## Record stage entry", text, name)
            self.assertIn(f"mokata progress mark {name}", text, name)

    def test_non_marking_skills_do_not_record_entry(self):
        # brainstorm/spec transitions are derived from checkpoints, not this writer.
        for name in ("brainstorm", "spec"):
            text = (self._TDIR / f"{name}.md").read_text(encoding="utf-8")
            self.assertNotIn("## Record stage entry", text, name)

    def test_shipped_skill_matches_generated_source(self):
        # The SKILL.md must stay byte-identical to what the builder renders (drift guard),
        # so the new section can't silently diverge between template and shipped skill.
        from mokata.agent_skills import load_skill_source, render_skill_md
        sdir = Path(__file__).resolve().parents[1] / "src" / "mokata" / "skills"
        for name in ("develop", "review", "ship"):
            shipped = (sdir / name / "SKILL.md").read_text(encoding="utf-8")
            regen = render_skill_md(load_skill_source(name, self._TDIR))
            self.assertEqual(shipped, regen, f"{name}/SKILL.md drifted")


# ============================================================ parity stays green
class TestParityGreen(unittest.TestCase):
    def test_progress_command_parity_holds(self):
        from mokata.parity import verify_parity
        self.assertTrue(verify_parity().ok, verify_parity().render())


if __name__ == "__main__":
    unittest.main()
