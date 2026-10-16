"""CTX-CONT (0.0.23 D10) — context continuity % measured in the G2 harness from the run archive:
a decision stated early must survive into develop's per-turn context.
"""

import json
import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (src/ on the path + provisioning refusal)

import _g2_fixture as FX

from bench.g2.archive import RunArchive
from bench.g2.continuity import continuity_report, develop_turn, measure_run, render_markdown
from bench.g2.streamparse import parse_stream
from bench.g2.study import render_study

DECISION = [{"id": "decimal-money", "text": "money is Decimal", "probe": ["decimal"]}]
COMPACT = {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "auto"}}
WRITE_DECIMAL = {"name": "Edit", "input": {"new_string": "from decimal import Decimal"}}
WRITE_FLOAT = {"name": "Write", "input": {"content": "x = float(y)"}}


def _tr(events):
    return parse_stream([json.dumps(e) for e in events])


def _hook(text):
    return {"type": "system", "subtype": "hook_response", "output": text}


class TestMeasureRun(unittest.TestCase):
    def test_no_compaction_is_trivial_survival_and_flagged_not_at_risk(self):
        tr = _tr([FX.assistant("a", 100, tools=[{"name": "Read"}]),
                  FX.assistant("b", 200, tools=[WRITE_DECIMAL])])
        self.assertEqual(develop_turn(tr), 2)
        (r,) = measure_run(tr, DECISION)
        self.assertFalse(r.at_risk)
        self.assertTrue(r.present)
        self.assertTrue(r.applied)

    def test_compaction_then_reinjection_survives(self):
        tr = _tr([FX.assistant("a", 100, tools=[{"name": "Read"}]), COMPACT,
                  _hook("mokata recall: decision — money uses Decimal"),
                  FX.assistant("b", 200, tools=[WRITE_DECIMAL])])
        (r,) = measure_run(tr, DECISION)
        self.assertTrue(r.at_risk)
        self.assertTrue(r.present)

    def test_compaction_without_reinjection_is_lost(self):
        tr = _tr([FX.assistant("a", 100, text="we will use Decimal", tools=[{"name": "Read"}]),
                  COMPACT, FX.assistant("b", 200, tools=[WRITE_FLOAT])])
        (r,) = measure_run(tr, DECISION)
        self.assertTrue(r.at_risk)
        self.assertFalse(r.present)              # the mention BEFORE the boundary does not count
        self.assertFalse(r.applied)

    def test_compaction_after_develop_is_not_at_risk(self):
        tr = _tr([FX.assistant("a", 100, tools=[WRITE_FLOAT]), COMPACT, FX.assistant("b", 50)])
        (r,) = measure_run(tr, DECISION)
        self.assertFalse(r.at_risk)
        self.assertFalse(r.applied)

    def test_a_run_that_never_writes_never_reached_develop(self):
        (r,) = measure_run(_tr([FX.assistant("a", 100, text="decimal")]), DECISION)
        self.assertIsNone(r.present)
        self.assertIsNone(r.develop_turn)

    def test_develop_marker_overrides_the_default_write_set(self):
        tr = _tr([FX.assistant("a", 100, tools=[{"name": "mcp__mokata__spec_check"}]),
                  FX.assistant("b", 100, tools=[WRITE_DECIMAL])])
        self.assertEqual(develop_turn(tr, "mcp__mokata__spec_check"), 1)


def _compacting(workdir, prompt, condition, repeat):
    """mokata re-injects the decision after compaction; bare does not."""
    ev = [FX.init_event(condition), FX.assistant("m1", 9000, tools=[{"name": "Read"}]), COMPACT]
    if condition != "bare":
        ev.append(_hook("mokata: decision decimal-money — money is decimal.Decimal"))
    ev += [FX.assistant("m2", 4000, tools=[WRITE_DECIMAL if condition != "bare" else WRITE_FLOAT]),
           FX.result_event(2)]
    return ev


class TestContinuityReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_trivial_runs_are_reported_as_untested(self):
        arch = RunArchive(FX.build_fixture_archive(os.path.join(self.tmp, "trivial"), repeats=5,
                                                   task_ids=["memory-decimal-decision"]))
        rep = continuity_report(arch)
        self.assertEqual(rep["tasks"], ["memory-decimal-decision"])
        self.assertEqual(rep["per_condition"]["mokata"]["continuity_pct"], 1.0)
        self.assertEqual(rep["per_condition"]["mokata"]["at_risk"], 0)
        self.assertIn("Continuity is UNTESTED by this run", render_markdown(rep))

    def test_continuity_discriminates_when_runs_compact(self):
        arch = RunArchive(FX.build_fixture_archive(os.path.join(self.tmp, "compact"), repeats=5,
                                                   task_ids=["memory-decimal-decision"],
                                                   script=_compacting))
        pc = continuity_report(arch)["per_condition"]
        self.assertEqual((pc["mokata"]["continuity_pct"], pc["bare"]["continuity_pct"]), (1.0, 0.0))
        self.assertEqual((pc["mokata"]["at_risk"], pc["bare"]["at_risk"]), (5, 5))
        self.assertEqual((pc["mokata"]["applied_pct"], pc["bare"]["applied_pct"]), (1.0, 0.0))
        text = render_markdown(continuity_report(arch))
        self.assertNotIn("UNTESTED", text)
        self.assertIn("## Context continuity (CTX-CONT)", render_study(arch))

    def test_a_run_without_seeded_decisions_says_so(self):
        arch = RunArchive(FX.build_fixture_archive(os.path.join(self.tmp, "none"), repeats=1,
                                                   task_ids=["gates-tdd-quantity-guard"]))
        self.assertIn("continuity not measured", render_markdown(continuity_report(arch)))


if __name__ == "__main__":
    unittest.main()
