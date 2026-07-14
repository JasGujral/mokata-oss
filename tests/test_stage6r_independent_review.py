"""Stage 6r — independent review closes the pipeline.

The closing `/mokata:review` runs as a FRESH-CONTEXT subagent by default (re-derives its
verdict from a self-contained brief, not the builder's context); the verdict is PERSISTED to
the SAME Stage-6b progress log as a `review_verdict` event (one persistence layer); and
`/mokata:ship` blocks on that persisted record instead of trusting conversation memory.

These tests assert:
  * `record_review_verdict` appends a `review_verdict` event `latest_review_verdict` reads back;
  * the verdict is run-scoped — a prior run's verdict never leaks into a fresh run's ship gate;
  * `ship_review_gate` BLOCKS when no verdict / on a failed review, PASSES (independent ✓) and
    PASSES (inline — not independent, never blocked) from the persisted record;
  * recording the verdict is UNGATED observability (the audit ledger is untouched);
  * `settings.review.independent` mirrors `brainstorm.auto` — default `on`, opt-`off`, degrade-clean;
  * the `mokata progress record-review` writer + `review-status` reader behave + exit correctly;
  * review.md gains `when_to_use` + the fresh-context/degrade clause + the record-verdict step, and
    REUSES the two-pass CONTENT verbatim; develop.md names review as the explicit required next step;
    ship.md verifies the persisted record; the regenerated SKILL.md drift guard + 54e parity hold.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR
from mokata.config import Surface
from mokata.govern import AuditLedger, PipelineCheckpoint
from mokata.progress_events import (
    REVIEW_VERDICT,
    ProgressLog,
    latest_review_verdict,
    record_review_verdict,
    review_independent_mode,
    ship_review_gate,
)
from mokata.skills import REVIEW_TWO_PASS_CONTENT

PHASES = ("brainstorm", "analysis", "strawman", "pre_mortem", "probes",
          "completeness_gate", "emit")


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _complete_run(surface, run_id="run-a"):
    cp = PipelineCheckpoint(surface.state, run_id)
    for p in PHASES:
        cp.mark_passed(p)
    return cp


def _set_setting(d, group, key, value):
    p = Path(d) / MOKATA_DIR / "manifest.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("settings", {}).setdefault(group, {})[key] = value
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ============================================================ persist + read the verdict
class TestReviewVerdictPersistence(unittest.TestCase):
    def test_record_then_read_passed_independent(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _complete_run(surface)
            e = record_review_verdict(surface, passed=True, independent=True)
            self.assertEqual(e["type"], REVIEW_VERDICT)
            self.assertEqual(e["stage"], "review")
            v = latest_review_verdict(surface, run_id="run-a")
            self.assertEqual(v, {"passed": True, "independent": True})

    def test_no_verdict_reads_none(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            self.assertIsNone(latest_review_verdict(surface))

    def test_latest_wins_over_earlier(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            record_review_verdict(surface, passed=False, independent=True, run_id="run-a")
            record_review_verdict(surface, passed=True, independent=True, run_id="run-a")
            self.assertTrue(latest_review_verdict(surface, run_id="run-a")["passed"])

    def test_findings_recorded_when_given(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            record_review_verdict(surface, passed=False, independent=True,
                                  findings=3, run_id="run-a")
            self.assertEqual(latest_review_verdict(surface, run_id="run-a")["findings"], 3)

    def test_verdict_is_run_scoped(self):
        # a PRIOR run's verdict must not leak into a fresh run's ship gate.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            record_review_verdict(surface, passed=True, independent=True, run_id="run-a")
            self.assertIsNone(latest_review_verdict(surface, run_id="run-b"))

    def test_runless_verdict_does_not_satisfy_a_run(self):
        # 6r-gate fix (doc-49 #3): a verdict recorded with run_id=None (outside any run)
        # must NOT satisfy a real run's gate — an active run requires an EXACT run match.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            record_review_verdict(surface, passed=True, independent=True, run_id=None)
            self.assertIsNone(latest_review_verdict(surface, run_id="run-a"),
                              "a run-less verdict must not match an active run")
            gate = ship_review_gate(surface, run_id="run-a")
            self.assertTrue(gate.blocks, "ship must still block for run-a")
            # with NO active run (run_id=None) the run-less verdict still counts (unchanged)
            self.assertIsNotNone(latest_review_verdict(surface, run_id=None))

    def test_degrade_clean_on_broken_surface(self):
        class Broken:
            state = None
        self.assertIsNone(latest_review_verdict(Broken()))


# ============================================================ ungated observability
class TestVerdictUngated(unittest.TestCase):
    def test_record_does_not_trip_the_write_gate(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            ledger = AuditLedger.from_mokata_dir(surface.mokata_dir)
            before = len(ledger.entries())
            record_review_verdict(surface, passed=True, independent=True)
            after = AuditLedger.from_mokata_dir(surface.mokata_dir).entries()
            self.assertEqual(len(after), before)
            self.assertFalse(any(e.get("kind") == "write_gate" for e in after))


# ============================================================ ship's evidence gate
class TestShipReviewGate(unittest.TestCase):
    def test_blocks_when_no_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _complete_run(surface)
            gate = ship_review_gate(surface)
            self.assertTrue(gate.blocks)
            self.assertFalse(gate.present)
            self.assertIn("review hasn't run", gate.message)

    def test_passes_independent(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _complete_run(surface)
            record_review_verdict(surface, passed=True, independent=True)
            gate = ship_review_gate(surface)
            self.assertFalse(gate.blocks)
            self.assertTrue(gate.independent)
            self.assertEqual(gate.message, "review passed (independent ✓)")

    def test_passes_inline_without_blocking(self):
        # a capability-degraded (inline) review still ships — but the difference is visible.
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _complete_run(surface)
            record_review_verdict(surface, passed=True, independent=False)
            gate = ship_review_gate(surface)
            self.assertFalse(gate.blocks)
            self.assertFalse(gate.independent)
            self.assertIn("inline — not independent", gate.message)

    def test_blocks_on_failed_review(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            _complete_run(surface)
            record_review_verdict(surface, passed=False, independent=True)
            gate = ship_review_gate(surface)
            self.assertTrue(gate.blocks)
            self.assertIn("review failed", gate.message)


# ============================================================ the config (brainstorm.auto twin)
class TestReviewIndependentConfig(unittest.TestCase):
    def test_default_on(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(review_independent_mode(_repo(d)), "on")

    def test_opt_off(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _set_setting(d, "review", "independent", "off")
            self.assertEqual(review_independent_mode(Surface.load(d)), "off")

    def test_unrecognised_reads_on(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _set_setting(d, "review", "independent", "sometimes")
            self.assertEqual(review_independent_mode(Surface.load(d)), "on")

    def test_degrade_clean(self):
        """A broken surface degrades to the STRONGER setting (`on` — the independent review).

        D5 narrowed the handler from `except Exception` to `except AttributeError`, so the double
        now raises what a REAL broken/half-built surface raises: `Surface.manifest` is a plain
        attribute, so a surface without one raises AttributeError — there is no code path on which
        it raises a RuntimeError, and a double that invents one was asserting that mokata swallows
        classes it can never actually see (which is how an `except Exception` hides a genuine typo).
        The behaviour under test is unchanged: broken ⇒ `on`, never a silent downgrade to inline."""
        class Broken:
            pass                        # no `.manifest` at all -> AttributeError, as in the wild
        self.assertEqual(review_independent_mode(Broken()), "on")


# ============================================================ the CLI writer + reader
class TestReviewCLI(unittest.TestCase):
    def _run(self, argv, cwd):
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

    def test_record_review_appends_event(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            rc, out = self._run(["progress", "record-review", "--passed", "--independent"], d)
            self.assertEqual(rc, 0)
            events = ProgressLog.from_surface(surface).read_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], REVIEW_VERDICT)
            self.assertEqual(events[0]["data"], {"passed": True, "independent": True})

    def test_record_review_requires_an_outcome(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            with self.assertRaises(SystemExit):        # neither --passed nor --failed
                self._run(["progress", "record-review"], d)

    def test_review_status_blocks_without_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, out = self._run(["progress", "review-status"], d)
            self.assertEqual(rc, 2)                     # non-zero -> ship must BLOCK
            self.assertIn("review hasn't run", out)

    def test_review_status_passes_independent(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self._run(["progress", "record-review", "--passed", "--independent"], d)
            rc, out = self._run(["progress", "review-status"], d)
            self.assertEqual(rc, 0)
            self.assertIn("independent ✓", out)

    def test_review_status_passes_inline(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self._run(["progress", "record-review", "--passed"], d)   # no --independent
            rc, out = self._run(["progress", "review-status"], d)
            self.assertEqual(rc, 0)                     # inline still ships (never hard-blocks)
            self.assertIn("inline — not independent", out)

    def test_review_status_blocks_on_failed(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            self._run(["progress", "record-review", "--failed", "--independent"], d)
            rc, out = self._run(["progress", "review-status"], d)
            self.assertEqual(rc, 2)
            self.assertIn("review failed", out)


# ============================================================ single-source templates
class TestTemplates(unittest.TestCase):
    _TDIR = Path(__file__).resolve().parents[1] / "src" / "mokata" / "templates" / "commands"

    def _text(self, name):
        return (self._TDIR / f"{name}.md").read_text(encoding="utf-8")

    def test_review_content_reused_verbatim(self):
        # WHO runs it changes, WHAT it checks does not — the two-pass content is verbatim.
        self.assertIn(REVIEW_TWO_PASS_CONTENT.strip(), self._text("review"))

    def test_review_has_when_to_use(self):
        text = self._text("review")
        self.assertIn("when_to_use:", text)
        self.assertIn("closing gate", text)
        self.assertIn("Do NOT engage mid-implementation", text)

    def test_review_runs_independent_by_default_with_honest_degrade(self):
        text = self._text("review")
        self.assertIn("FRESH-CONTEXT subagent", text)
        self.assertIn("SELF-CONTAINED brief", text)
        self.assertIn("review: inline — this harness has no subagents", text)
        self.assertIn("settings.review.independent=off", text)

    def test_review_records_its_verdict(self):
        text = self._text("review")
        self.assertIn("## Record verdict", text)
        self.assertIn("mokata progress record-review --passed", text)
        self.assertIn("--independent", text)

    def test_develop_names_review_as_explicit_required_next_step(self):
        text = self._text("develop")
        self.assertIn("## Next step", text)
        self.assertIn("/mokata:review` (required before ship)", text)
        # the section names review explicitly AND forbids the generic placeholder for it.
        section = text.split("## Next step", 1)[1]
        self.assertIn("do NOT use the generic `/mokata:<next>` placeholder", section)

    def test_ship_verifies_the_persisted_record(self):
        text = self._text("ship")
        self.assertIn("mokata progress review-status", text)
        self.assertIn("PERSISTED RECORD", text)
        self.assertIn("review passed (independent ✓)", text)
        self.assertIn("audit ledger", text)

    def test_only_review_records_a_verdict(self):
        for name in ("develop", "ship", "brainstorm", "spec"):
            self.assertNotIn("## Record verdict", self._text(name), name)

    def test_shipped_skill_matches_generated_source(self):
        from mokata.agent_skills import load_skill_source, render_skill_md
        sdir = Path(__file__).resolve().parents[1] / "src" / "mokata" / "skills"
        for name in ("develop", "review", "ship"):
            shipped = (sdir / name / "SKILL.md").read_text(encoding="utf-8")
            regen = render_skill_md(load_skill_source(name, self._TDIR))
            self.assertEqual(shipped, regen, f"{name}/SKILL.md drifted")


# ============================================================ parity stays green
class TestParityGreen(unittest.TestCase):
    def test_parity_holds(self):
        from mokata.parity import verify_parity
        self.assertTrue(verify_parity().ok, verify_parity().render())


if __name__ == "__main__":
    unittest.main()
