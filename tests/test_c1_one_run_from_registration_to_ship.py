"""0.0.22 stage C1 — `session-truth`: one run identity from registration through ship.

Rows (doc 84): `B-LIFE-FU` (a real ship-completion event, MCP parity for `progress mark`, and the
dead `STAGE_PASS` resolved — deleted, since nothing ever wrote it) and, in
`test_a40_…ThePublisherKeepsItsOwnHighWaterMark`, `PUBLISH-POINTER-IS-BORROWED-MEMORY`.

Plus one same-class sweep found while grading this stage's mutants: a mutant LABEL is a
double-quoted shell word, so a name written in backticks inside one is a command substitution
(`MutantLabelsAreNotCommands`).

`RUN-REG` (structural) and `SIGNAL-1-IS-DEAD-BEFORE-A-RUN-IS-REGISTERED` are NOT closed here. Both
need a ruling first (see the stage report); nothing in this file claims them.
"""
import glob
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)
import _mutant_driver_contract as MDC

from mokata import progress_events as PE
from mokata.cli import main
from mokata.config import Surface
from mokata.govern.resume import CHECKPOINT_PREFIX, PipelineCheckpoint
from mokata.init import init_repo
from mokata.progress import _shipped_run_ids
from mokata.state import StateStore
from mokata.tdd_state import state_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "mokata")


def _silent(_):
    pass


def _repo_with_runs(d, *run_ids):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    surface = Surface.load(d)
    for rid in run_ids:
        PipelineCheckpoint(surface.state, rid).ensure_registered()
    return Surface.load(d)


def _cli(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(list(argv))
    return rc, buf.getvalue()


class BLifeFuThereIsAShipCompletionEvent(unittest.TestCase):

    def test_b_life_fu_stage_pass_is_gone_and_run_finished_replaced_it(self):
        """`STAGE_PASS` was defined from 0.0.16 and written by nothing. No-backcompat: deleted."""
        self.assertFalse(hasattr(PE, "STAGE_PASS"))
        self.assertIn(PE.RUN_FINISHED, PE.PROGRESS_EVENT_TYPES)
        self.assertEqual(("merged", "pr", "kept", "discarded"), PE.LANDINGS)

    def test_a_finish_is_recorded_keyed_to_the_run_and_stamps_completed_at(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo_with_runs(d, "r1")
            event = PE.record_run_finished(surface, landing="pr")
            self.assertEqual(("run_finished", "ship", "r1"),
                             (event["type"], event["stage"], event["run_id"]))
            self.assertEqual({"landing": "pr"}, event["data"])
            stamped = StateStore(state_dir(d)).read(CHECKPOINT_PREFIX + "r1")
            self.assertTrue(stamped.get("completed_at"), "the finish did not stamp completed_at")
            self.assertIn("r1", _shipped_run_ids(StateStore(state_dir(d))))

    def test_a_finish_is_never_recorded_run_less(self):
        """Two runs, neither identifiably this caller's: refuse, write NOTHING."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo_with_runs(d, "r1", "r2")
            log = PE.ProgressLog.from_surface(surface)
            before = len(log.read_events(tail=None))
            with self.assertRaises(PE.FinishRefused):
                PE.record_run_finished(surface, landing="merged")
            self.assertEqual(before, len(log.read_events(tail=None)))

    def test_a_finish_names_its_landing(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo_with_runs(d, "r1")
            for bad in ("", "shipped", "MERGED"):
                with self.subTest(landing=bad), self.assertRaises(PE.FinishRefused):
                    PE.record_run_finished(surface, landing=bad)

    def test_re_entering_develop_after_a_finish_reads_as_active_again(self):
        """The backward scan's forward-only view holds for the new event too."""
        with tempfile.TemporaryDirectory() as d:
            surface = _repo_with_runs(d, "r1")
            PE.record_run_finished(surface, landing="kept")
            PE.ProgressLog.from_surface(surface).append_event(PE.STAGE_ENTER, "develop",
                                                              run_id="r1")
            self.assertNotIn("r1", _shipped_run_ids(StateStore(state_dir(d))))

    def test_the_cli_finish_records_and_its_refusal_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as d:
            _repo_with_runs(d, "r1", "r2")
            rc, out = _cli("progress", "finish", "--landing", "merged", "--path", d)
            self.assertEqual(1, rc, "a refused finish exited 0: a finish that did not land "
                                    "reads as one that did")
            self.assertIn("did NOT record", out)
            rc, out = _cli("progress", "finish", "--landing", "merged", "--run", "r2",
                           "--path", d)
            self.assertEqual(0, rc, out)
            self.assertIn("run r2 finished (merged)", out)


class BLifeFuTheHarnessHasParity(unittest.TestCase):

    def test_b_life_fu_progress_mark_and_run_finish_are_registered_mcp_tools(self):
        from mokata.mcp import registry as REG
        names = {t.name: t.kind for t in REG.TOOLS}
        self.assertEqual("read", names.get("progress_mark"))
        self.assertEqual("read", names.get("run_finish"))

    def test_the_parity_matrix_declares_them_on_progress(self):
        from mokata.parity import declared_mcp_tools, verify_parity
        self.assertTrue({"progress_mark", "run_finish"} <= declared_mcp_tools())
        report = verify_parity()
        self.assertTrue(report.ok, report)

    def test_the_mcp_twins_say_what_the_cli_says(self):
        from mokata.mcp.tools_read import progress_mark, run_finish
        with tempfile.TemporaryDirectory() as d:
            _repo_with_runs(d, "r1")
            marked = progress_mark(path=d, stage="ship")
            self.assertTrue(marked["recorded"], marked)
            self.assertEqual("r1", marked["run"])
            done = run_finish(path=d, landing="discarded")
            self.assertTrue(done["recorded"], done)
            self.assertEqual("mokata progress: run r1 finished (discarded).", done["message"])
            self.assertFalse(run_finish(path=d, landing="")["recorded"])
            self.assertFalse(progress_mark(path=d, stage="nowhere")["recorded"])

    def test_the_mcp_mark_refuses_an_ambiguous_run_like_the_cli(self):
        from mokata.mcp.tools_read import progress_mark
        with tempfile.TemporaryDirectory() as d:
            _repo_with_runs(d, "r1", "r2")
            res = progress_mark(path=d, stage="develop")
            self.assertFalse(res["recorded"])
            self.assertEqual("refused", res["status"])

    def test_ship_prose_records_the_finish_and_every_stage_names_the_tool(self):
        """The skills drive it; a finish nobody is told to record is a dead event again."""
        with io.open(os.path.join(SRC, "skills", "ship", "SKILL.md"), encoding="utf-8") as fh:
            ship = fh.read()
        self.assertIn("`run_finish` MCP tool", ship)
        self.assertIn("mokata progress finish --landing", ship)
        for stage in ("develop", "review", "ship"):
            with self.subTest(stage=stage):
                path = os.path.join(SRC, "skills", stage, "SKILL.md")
                with io.open(path, encoding="utf-8") as fh:
                    self.assertIn("`progress_mark` MCP tool with stage `%s`" % stage, fh.read())


class MutantLabelsAreNotCommands(unittest.TestCase):

    def _corpus(self):
        # CORPUS: THE WORKING TREE — the drivers a person can run from this checkout.
        out = {}
        for p in glob.glob(os.path.join(ROOT, "tests", "_*mutants*.sh")):
            with open(p, encoding="utf-8") as fh:
                out[os.path.basename(p)] = fh.read()
        return out

    def test_no_mutant_label_runs_a_command_substitution(self):
        self.assertEqual({}, MDC.label_substitutions(self._corpus()))

    def test_the_check_convicts_the_shape_that_shipped(self):
        planted = 'mutant "A04 `max_seq` swallows into 0" "$ST" \\\n  a b "$T"\n'
        self.assertEqual({"d.sh": [1]}, MDC.label_substitutions({"d.sh": planted}))
        multi = 'mutant "N02 first line\n  then `if` on the second" "$T" a b\n'
        self.assertEqual({"d.sh": [2]}, MDC.label_substitutions({"d.sh": multi}))
        dollar = 'mutant "X $(date) y" "$T" a b\n'
        self.assertEqual({"d.sh": [1]}, MDC.label_substitutions({"d.sh": dollar}))

    def test_an_escaped_backtick_and_the_mutant_body_are_not_labels(self):
        ok = 'mutant "A04 \\`max_seq\\` swallows" "$ST" \\\n  \'x = `y`\' \'z\' "$T"\n'
        self.assertEqual({}, MDC.label_substitutions({"d.sh": ok}))


if __name__ == "__main__":
    unittest.main()
