"""0.0.23 stage D5 — `spec-quality`: SQ.S1 + SQ-REGRESS.

SQ.S1 — before a spec reaches the human approval gate it runs a battery composed from existing
members (structural · rules · coherence · failure-mode); a failure is a set of structured findings,
the author revises, and the loop is bounded at ≤3 cycles before it escalates to the human. Each
cycle is ledgered. Only a PASSING spec reaches consent.
SQ-REGRESS — a finding whose root cause is the APPROACH force-regresses the run spec → brainstorm.
"""
import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _plan import (call_planned, commit_planned, emit_planned,  # noqa: F401,E402  (R2)
                   planned, sealed, with_tasks)

from mokata.brainstorm import APPROACH_STATE_KEY, PIPELINE_PHASES, Handoff
from mokata.engine import spec_quality as SQ
from mokata.engine.emit import emit_spec
from mokata.engine.spec import AcceptanceCriterion, CodeAnchor, Spec, TestRef
from mokata.govern.resume import PipelineCheckpoint
from mokata.state import StateStore


def _handoff(cons=(), decisions=(), fit=None, name="A"):
    d = {"topic": "limits", "phase": "brainstorm", "approver": "jas", "approved_at": "now",
         "approach": {"name": name, "summary": "s", "cons": list(cons),
                      "decisions": [dict(x) for x in decisions]},
         "answered_questions": [], "grounding": {}}
    if fit:
        d["design_fit"] = {"approach": name, "verdict": fit, "risks": ["layering"]}
    return Handoff.from_dict(d)


DEFERRING = {"id": "D1", "statement": "no batch", "about_code": [],
             "deferred": [{"id": "X1", "item": "batch update", "paths": [],
                           "markers": ["batch_update"]}]}


def _spec(*texts, approach=None):
    return Spec("t", [AcceptanceCriterion(f"AC-{i + 1}", t) for i, t in enumerate(texts)],
                approach=approach)


def _tests(spec):
    return [TestRef(f"test_{c.id}", [c.id]) for c in spec.criteria]


class _Ledger:
    def __init__(self):
        self.rows = []

    def record(self, kind, **fields):
        self.rows.append((kind, fields))


class TheBattery(unittest.TestCase):

    def test_a_clean_spec_passes_every_member(self):
        spec = _spec("limits reject a value over the max")
        self.assertEqual([], [f for f in SQ.run_battery(spec, _tests(spec)) if f.blocking])

    def test_structural_unmapped_and_untestable(self):
        spec = _spec("x", "")
        codes = {f.code for f in SQ.run_battery(spec, [TestRef("t", ["AC-1"])])}
        self.assertEqual({"completeness", "untestable"}, codes)

    def test_failure_mode_a_declared_downside_needs_an_ac(self):
        h = _handoff(cons=["cache invalidation lag"])
        spec = _spec("limits apply")
        found = SQ.run_battery(spec, _tests(spec), handoff=h, enforce_failure_modes=True)
        blocking = [f for f in found if f.blocking]
        self.assertEqual(["uncovered-risk"], [f.code for f in blocking])
        self.assertTrue(all(not f.blocking for f in found if f.code == "angle-unaddressed"),
                        "the standard angles are advisory")
        for answered in ("invalidation of the cache stays bounded", "covers P1 explicitly"):
            spec = _spec(answered)
            self.assertEqual([], [f for f in SQ.run_battery(spec, _tests(spec), handoff=h,
                                                             enforce_failure_modes=True)
                                  if f.blocking])

    def test_failure_mode_is_advisory_by_default_the_open_ruling(self):
        self.assertFalse(SQ.ENFORCE_FAILURE_MODES)
        h = _handoff(cons=["cache invalidation lag"])
        spec = _spec("limits apply")
        found = SQ.run_battery(spec, _tests(spec), handoff=h)
        self.assertEqual([("uncovered-risk", False)],
                         [(f.code, f.blocking) for f in found if f.code == "uncovered-risk"])

    def test_coherence_is_ADVISORY_by_default_and_never_regresses(self):
        """0.0.23 verification M3: shipped advisory — a spec 0.0.22 emitted is not refused, and
        nothing rolls the run back before a human sees a proposal (ruling owed)."""
        with tempfile.TemporaryDirectory() as d:
            store, ledger = StateStore(d), _Ledger()
            cp = PipelineCheckpoint(store, "r1")
            for p in PIPELINE_PHASES:
                cp.mark_passed(p)
            store.write(APPROACH_STATE_KEY, _handoff(decisions=[DEFERRING]).to_dict())
            spec = _spec("supports batch_update of rows")
            v = SQ.quality_gate(spec, _tests(spec), store=store, run_id="r1", ledger=ledger)
            self.assertEqual(SQ.PASS, v.status)
            self.assertIn("requires-deferred", [f.code for f in v.findings])
            self.assertNotEqual("brainstorm", PipelineCheckpoint(store, "r1").resume_phase())

    def test_an_approach_name_that_differs_only_in_spelling_is_not_a_mismatch(self):
        spec = _spec("x", approach="Thin  adapter")
        codes = [f.code for f in SQ.run_battery(spec, _tests(spec),
                                                handoff=_handoff(name="thin-adapter"),
                                                enforce_coherence=True)]
        self.assertNotIn("approach-mismatch", codes)

    def test_coherence_the_named_approach_must_be_the_approved_one(self):
        spec = _spec("x", approach="B")
        codes = [f.code for f in SQ.run_battery(spec, _tests(spec), handoff=_handoff(),
                                                enforce_coherence=True) if f.blocking]
        self.assertEqual(["approach-mismatch"], codes)

    def test_coherence_requiring_a_deferred_item_is_an_approach_root_cause(self):
        spec = _spec("supports batch_update of rows")
        f = [f for f in SQ.run_battery(spec, _tests(spec), handoff=_handoff(decisions=[DEFERRING]),
                                       enforce_coherence=True) if f.blocking][0]
        self.assertEqual(("requires-deferred", SQ.ROOT_APPROACH), (f.code, f.root_cause))

    def test_a_misfit_approach_is_an_approach_root_cause(self):
        spec = _spec("x")
        roots = {f.root_cause for f in SQ.run_battery(spec, _tests(spec),
                                                       handoff=_handoff(fit="misfit"),
                                                       enforce_coherence=True)
                 if f.blocking}
        self.assertEqual({SQ.ROOT_APPROACH}, roots)

    def test_rules_overlap_and_unresolved_anchors_are_advisory(self):
        with tempfile.TemporaryDirectory() as d:
            saved = Spec("other", [AcceptanceCriterion("AC-1", "limits module behaviour")])
            spec = Spec("t", [AcceptanceCriterion("AC-1", "x", anchors=[
                CodeAnchor("src/limits.py", "check")])])
            found = SQ.run_battery(spec, _tests(spec), corpus=[saved], root=d)
            self.assertTrue(found)
            self.assertTrue(all(not f.blocking for f in found))
            self.assertIn("overlap", {f.code for f in found})
            self.assertIn("anchor-unresolved", {f.code for f in found})


class TheLoop(unittest.TestCase):

    def test_revise_then_escalate_at_three_and_every_cycle_is_ledgered(self):
        with tempfile.TemporaryDirectory() as d:
            store, ledger = StateStore(d), _Ledger()
            spec = _spec("")
            statuses = [SQ.quality_gate(spec, _tests(spec), store=store, run_id="r1",
                                        ledger=ledger).status for _ in range(4)]
            self.assertEqual([SQ.REVISE, SQ.REVISE, SQ.ESCALATE, SQ.ESCALATE], statuses)
            self.assertEqual(4, sum(1 for k, _ in ledger.rows if k == SQ.LEDGER_KIND))
            self.assertEqual(["untestable:AC-1"], ledger.rows[0][1]["findings"])

    def test_a_pass_resets_the_loop(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(d)
            bad, good = _spec(""), _spec("x")
            SQ.quality_gate(bad, _tests(bad), store=store, run_id="r1")
            SQ.quality_gate(bad, _tests(bad), store=store, run_id="r1")
            self.assertTrue(SQ.quality_gate(good, _tests(good), store=store, run_id="r1").passed)
            self.assertEqual(0, SQ.cycles_so_far(store, "r1"))
            self.assertEqual(SQ.REVISE, SQ.quality_gate(bad, _tests(bad), store=store,
                                                        run_id="r1").status)

    def test_runs_count_their_own_cycles(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(d)
            bad = _spec("")
            for _ in range(3):
                SQ.quality_gate(bad, _tests(bad), store=store, run_id="r1")
            self.assertEqual(SQ.REVISE, SQ.quality_gate(bad, _tests(bad), store=store,
                                                        run_id="r2").status)


class SqRegress(unittest.TestCase):

    def test_an_approach_root_cause_force_regresses_spec_to_brainstorm(self):
        with tempfile.TemporaryDirectory() as d:
            store, ledger = StateStore(d), _Ledger()
            cp = PipelineCheckpoint(store, "r1")
            for p in PIPELINE_PHASES:
                cp.mark_passed(p)
            store.write(APPROACH_STATE_KEY, _handoff(decisions=[DEFERRING]).to_dict())
            spec = _spec("supports batch_update of rows")
            v = SQ.quality_gate(spec, _tests(spec), store=store, run_id="r1", ledger=ledger,
                                enforce_coherence=True)
            self.assertEqual(SQ.REGRESS, v.status)
            self.assertEqual("brainstorm", PipelineCheckpoint(store, "r1").resume_phase())
            marker = store.read(SQ.REGRESS_PREFIX + "r1")
            self.assertEqual("requires-deferred", marker["findings"][0]["code"])
            self.assertIn(SQ.REGRESS_LEDGER_KIND, [k for k, _ in ledger.rows])

    def test_a_spec_rooted_failure_never_regresses(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(d)
            cp = PipelineCheckpoint(store, "r1")
            for p in PIPELINE_PHASES:
                cp.mark_passed(p)
            spec = _spec("")
            SQ.quality_gate(spec, _tests(spec), store=store, run_id="r1")
            self.assertFalse(store.exists(SQ.REGRESS_PREFIX + "r1"))
            self.assertTrue(PipelineCheckpoint(store, "r1").is_complete())


class OnlyAPassingSpecReachesConsent(unittest.TestCase):

    def _surface(self, d):
        return mock.Mock(root=d, state=StateStore(d))

    def test_a_failing_battery_refuses_the_emit_before_the_human_is_asked(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self._surface(d)
            spec = _spec("")
            asked = []
            out = emit_planned(surface, spec, _tests(spec), run_id="r1",
                            confirm=lambda t: asked.append(t) or True)
            self.assertFalse(out.committed)
            self.assertTrue(out.blocked_by_quality)
            self.assertEqual([], asked, "the human is never shown a spec that failed the battery")
            self.assertIn("untestable", [f.code for f in out.quality.blocking])

    def test_a_passing_spec_emits_as_before_and_carries_its_advisories(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self._surface(d)
            surface.state.write(APPROACH_STATE_KEY, _handoff(cons=["lag"]).to_dict())
            spec = _spec("the lag stays under a second")
            out = emit_planned(surface, spec, _tests(spec), run_id="r1", assume_yes=True)
            self.assertTrue(out.committed, out.reason)
            self.assertTrue(out.quality.passed)
            self.assertIn("angle-unaddressed", [f.code for f in out.quality.advisories])
            self.assertFalse(any("probe P" in w for w in out.warnings),
                             "advisories ride the verdict and the ledger, not the success text")

    def test_a_standalone_spec_is_judged_on_structure_alone(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self._surface(d)
            spec = _spec("x")
            out = emit_planned(surface, spec, _tests(spec), run_id="r1", assume_yes=True)
            self.assertTrue(out.committed, out.reason)

    def test_the_mcp_surface_refuses_with_the_same_findings(self):
        from mokata.mcp import tools_spec as T
        v = SQ.QualityVerdict(SQ.REVISE, [SQ.QualityFinding("structural", "untestable", "m")], 1)
        with mock.patch("mokata.engine.emit.run_quality_gate", return_value=v), \
                mock.patch.object(T, "_surface", return_value=mock.Mock(root=".", mokata_dir=".")), \
                mock.patch.object(T, "_evidence_store", return_value=(mock.Mock(), "r1")), \
                mock.patch("mokata.engine.completeness.run_completeness_gate") as gate, \
                mock.patch("mokata.engine.emit.structural_backend_available", return_value=False), \
                mock.patch("mokata.brainstorm.load_approved_approach", return_value=None), \
                mock.patch.object(T, "AuditLedger"):
            gate.return_value = mock.Mock(passed=True, warnings=[])
            out = call_planned(T.spec_emit, title="t", criteria=[{"id": "AC-1", "text": ""}],
                              tests=[{"name": "t", "ac_ids": ["AC-1"]}])
        self.assertEqual(("blocked", "spec-quality"), (out["status"], out["gate"]))
        self.assertEqual("untestable", out["quality"]["findings"][0]["code"])
        self.assertIn("No spec was written", out["hint"])


class TheProse(unittest.TestCase):

    def test_spec_skill_and_template_carry_the_loop(self):
        root = os.path.join(os.path.dirname(__file__), "..", "src", "mokata")
        for rel in (("templates", "commands", "spec.md"), ("skills", "spec", "SKILL.md")):
            with open(os.path.join(root, *rel), "rb") as fh:
                body = fh.read().decode("utf-8")
            self.assertIn("Spec-quality loop:", body)
            self.assertIn("regressed to BRAINSTORM", body)


if __name__ == "__main__":
    unittest.main()
