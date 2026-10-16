"""PIPE-TOKEN-AUDIT (0.0.23 closing instrument) — the per-phase graph-compliance table and the
system-prompt-size tracker, generated from a G2 run archive. The NAMED report needs a real run;
over a fixture this renders a bannered template and refuses to write history.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _support  # noqa: F401  (src/ on the path + provisioning refusal)

import _g2_fixture as FX

from bench.g2 import __main__ as cli
from bench.g2.archive import ArchiveError, RunArchive
from bench.g2.audit import (UNPHASED, append_history, audit, nav_class, phase_signal, phases_of,
                            render_report, status_of)
from bench.g2.streamparse import parse_stream


def _tr(events):
    return parse_stream([json.dumps(e) for e in events])


def _phased(workdir, prompt, condition, repeat):
    """mokata: spec navigates via the graph; develop falls back to Grep (the defect to catch).
    bare: no phases at all."""
    ev = [FX.init_event(condition)]
    if condition == "bare":
        ev += [FX.assistant("b1", 9000, tools=[{"name": "Grep", "input": {"pattern": "x"}}]),
               FX.tool_result("b1-t0", "hit"), FX.assistant("b2", 11000)]
    else:
        ev += [FX.assistant("m1", 12000, tools=[{"name": "Skill", "input": {"skill": "mokata:spec"}}]),
               {"type": "system", "subtype": "hook_response", "output": "x" * 4000},   # 1000 tok
               FX.assistant("m2", 12500, tools=[{"name": "mcp__mokata__query", "input": {}}]),
               FX.tool_result("m2-t0", "graph answer"),
               FX.assistant("m3", 13000, tools=[{"name": "SlashCommand",
                                                 "input": {"command": "/mokata:develop"}}]),
               FX.assistant("m4", 13500, tools=[{"name": "Bash", "input": {"command": "rg line_total shop"}}]),
               FX.tool_result("m4-t0", "shop/cart.py"),
               FX.assistant("m5", 14000)]
    ev.append(FX.result_event(5))
    return ev


class TestClassification(unittest.TestCase):
    def test_navigation_classes(self):
        self.assertEqual(nav_class({"name": "mcp__mokata__query"}), "graph")
        self.assertEqual(nav_class({"name": "Bash", "input": {"command": "mokata query callers x"}}), "graph")
        self.assertEqual(nav_class({"name": "Grep"}), "grep")
        self.assertEqual(nav_class({"name": "Bash", "input": {"command": "cd s && grep -rn x ."}}), "grep")
        self.assertEqual(nav_class({"name": "Bash", "input": {"command": "python -m pytest"}}), None)
        self.assertEqual(nav_class({"name": "Read"}), None)

    def test_phase_signals(self):
        self.assertEqual(phase_signal({"name": "Skill", "input": {"skill": "mokata:review"}}), "review")
        self.assertEqual(phase_signal({"name": "SlashCommand", "input": {"command": "/spec"}}), "spec")
        self.assertEqual(phase_signal({"name": "mcp__mokata__spec_emit"}), "spec")
        self.assertIsNone(phase_signal({"name": "Skill", "input": {"skill": "pdf"}}))

    def test_status(self):
        self.assertEqual(status_of(0, 2), "GREP FLOOR")
        self.assertEqual(status_of(1, 2), "mixed")
        self.assertEqual(status_of(1, 0), "graph")
        self.assertEqual(status_of(0, 0), "no navigation")

    def test_a_phase_signal_takes_effect_from_the_next_turn(self):
        ph = phases_of(_tr(_phased("w", "p", "mokata", 1)))
        self.assertEqual(ph[UNPHASED]["turns"], 1)
        self.assertEqual(ph["spec"]["graph"], 1)
        self.assertEqual(ph["develop"]["grep"], 1)
        self.assertEqual(ph["spec"]["injected"], [1000])


class TestTheAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.out = FX.build_fixture_archive(os.path.join(cls.tmp, "run"), repeats=5, script=_phased,
                                           task_ids=["graph-rename-line-total"])
        cls.arch = RunArchive(cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_grep_floor_phase_is_named(self):
        rep = audit(self.arch)
        self.assertEqual(rep["phases_on_grep_floor"], ["mokata:develop"])
        rows = {r["phase"]: r for r in rep["by_condition"]["mokata"]["phases"]}
        self.assertEqual((rows["spec"]["status"], rows["develop"]["status"]), ("graph", "GREP FLOOR"))
        self.assertEqual(rows["develop"]["grep_floor_runs"], 5)

    def test_injected_context_is_checked_against_the_documented_budget(self):
        rows = {r["phase"]: r for r in audit(self.arch)["by_condition"]["mokata"]["phases"]}
        self.assertEqual(rows["spec"]["max_injected_tokens"], 1000)
        self.assertEqual(rows["spec"]["injections_over_turn_budget"], 5)

    def test_system_prompt_tracker(self):
        sp = {c: v["system_prompt"] for c, v in audit(self.arch)["by_condition"].items()}
        self.assertEqual(sp["mokata"]["median_first_turn_context"], 12000)
        self.assertEqual(sp["bare"]["median_first_turn_context"], 9000)
        self.assertEqual(sp["mokata"]["median_tools_offered"], 10)

    def test_fixture_report_is_bannered_as_not_the_audit(self):
        text = render_report(self.arch)
        self.assertIn("FIXTURE RUN — NOT THE AUDIT", text)
        self.assertIn("requires a real G2 run", text)
        self.assertIn("**GREP FLOOR**", text)
        self.assertIn("❌ phases on the grep floor", text)

    def test_history_refuses_a_fixture(self):
        hist = os.path.join(self.tmp, "history.json")
        with self.assertRaises(ArchiveError):
            append_history(self.arch, hist)
        self.assertFalse(os.path.exists(hist))

    def test_history_appends_a_shape_real_run_release_over_release(self):
        dst = os.path.join(self.tmp, "shape-real")
        shutil.copytree(self.out, dst)
        mp = os.path.join(dst, "run.json")
        with open(mp, encoding="utf-8") as fh:
            m = json.load(fh)
        m["agent"]["id"], m["judge"]["id"], m["cloner"], m["kind"] = \
            "claude-code", "claude-cli", "git", "real"
        with open(mp, "w", encoding="utf-8") as fh:
            json.dump(m, fh)
        hist = os.path.join(self.tmp, "h2.json")
        append_history(RunArchive(dst), hist)
        rows = append_history(RunArchive(dst), hist)
        self.assertEqual(len(rows), 2)
        self.assertIn("### Release over release", render_report(RunArchive(dst), rows))

    def test_cli_writes_the_report_and_refuses_fixture_history(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(cli.main(["audit", "--run", self.out]), 0)
            self.assertEqual(cli.main(["audit", "--run", self.out, "--history",
                                       os.path.join(self.tmp, "h3.json")]), 2)
        self.assertTrue(os.path.exists(os.path.join(self.out, "PIPE-TOKEN-AUDIT.md")))
        self.assertIn("REFUSING to append", err.getvalue())


if __name__ == "__main__":
    unittest.main()
