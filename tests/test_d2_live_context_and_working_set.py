"""0.0.22 stage D2 — `live-context-and-working-set`: HP.S1 + HP.S2.

HP.S1 — the briefing says what the repo looks like NOW (git, the test command, instruction files),
≤6 lines, and that block is the FIRST thing dropped when the briefing is over budget.
HP.S2 — one run-keyed `WorkingSet`: references derived from their owners on every read (never
copied), plus the extras with no other home (touched files, notes), keyed by the run this SESSION is
bound to so parallel runs never mix their files.
"""
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import hook_cli
from mokata import live_context as LC
from mokata import run_intent as RI
from mokata import working_set as WS
from mokata.bootstrap import build_bootstrap
from mokata.cli import main
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo
from mokata.run_resolver import APPROACH_PREFIX, SPEC_PREFIX
from mokata.state import StateStore
from mokata.tdd_state import state_dir


def _silent(_):
    pass


def _repo(d, *runs):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    surface = Surface.load(d)
    for rid in runs:
        PipelineCheckpoint(surface.state, rid).ensure_registered()
    return Surface.load(d)


def _git(d, *args):
    subprocess.run(["git", "-C", d, *args], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)


class HpS1TheBriefingSaysWhereTheRepoIsNow(unittest.TestCase):

    def test_hp_s1_git_tests_and_instructions_reach_the_briefing(self):
        with tempfile.TemporaryDirectory() as d:
            _git(d, "init", "-q", "-b", "main")
            surface = _repo(d)
            with open(os.path.join(d, "CLAUDE.md"), "w", encoding="utf-8") as fh:
                fh.write("# rules\n")
            with open(os.path.join(d, "new.py"), "w", encoding="utf-8") as fh:
                fh.write("x = 1\n")
            text = build_bootstrap(Surface.load(d)).text
            self.assertIn(LC.HEADER, text)
            self.assertIn("- git: main", text)
            self.assertIn("untracked", text)
            self.assertIn("no test command set", text, "an unset command must SAY so")
            self.assertIn("- instructions: CLAUDE.md", text)
            self.assertTrue(surface)

    def test_the_configured_test_command_is_named_never_guessed(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            with mock.patch("mokata.baseline.baseline_command", return_value="make test"):
                self.assertEqual("- tests: `make test`", LC.test_line(surface.manifest))

    def test_outside_git_the_git_line_is_absent_not_wrong(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(LC.git_line(d))

    def test_the_block_is_capped_at_six_lines(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            lines = LC.live_context_lines(surface, structure_line="x" * 10)
            self.assertLessEqual(len(lines), LC.MAX_LINES)

    def test_over_budget_the_live_block_drops_FIRST_and_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            _git(d, "init", "-q", "-b", "main")
            _repo(d)
            surface = Surface.load(d)
            full = build_bootstrap(surface).text
            budget = (len(full) - 20) // 4          # just under what the full briefing needs
            slim = build_bootstrap(surface, budget=budget).text
            self.assertNotIn(LC.HEADER, slim)
            self.assertIn(LC.DROPPED_NOTICE, slim)
            self.assertIn("Active gates (inviolable):", slim, "the gates were cut before the "
                                                               "convenience block")


class HpS2OneKeyedWorkingSetPerRun(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_hp_s2_references_are_derived_from_their_owners_not_copied(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            store = StateStore(state_dir(d))
            ws = WS.working_set(d, "rA")
            self.assertIsNone(ws.spec_key)
            store.write(APPROACH_PREFIX + "rA", {"topic": "Cache layer"})
            store.write(SPEC_PREFIX + "rA", {"run_id": "rA"})
            ws = WS.working_set(d, "rA")
            self.assertEqual(SPEC_PREFIX + "rA", ws.spec_key)
            self.assertEqual(APPROACH_PREFIX + "rA", ws.approach_key)
            self.assertTrue(ws.plan_slug)
            self.assertFalse(store.exists(WS.WORKING_SET_PREFIX + "rA"),
                             "a reference was COPIED into the working set — a second truth")

    def test_touched_files_are_deduplicated_capped_and_the_drop_counted(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            with mock.patch.object(WS, "TOUCHED_CAP", 3):
                for p in ("a.py", "b.py", "a.py", "c.py", "d.py"):
                    WS.record_touched(d, "rA", p)
            ws = WS.working_set(d, "rA")
            # a re-write MOVES the path to the newest end, so the cap drops the oldest UNIQUE one (b)
            self.assertEqual(["a.py", "c.py", "d.py"], ws.touched_files)
            self.assertEqual(1, ws.touched_dropped)

    def test_the_dirty_track_hook_records_into_the_SESSIONS_run_only(self):
        """Parallel runs: S1's writes land in rA, S2's in rB, and an unbound session records none."""
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA", "rB")
            RI.bind_registered(d, "S1", "rA")
            RI.bind_registered(d, "S2", "rB")
            for sid, f in (("S1", "src/a.py"), ("S2", "src/b.py"), ("S9", "src/z.py")):
                payload = {"session_id": sid, "cwd": d, "tool_name": "Write",
                           "tool_input": {"file_path": os.path.join(d, f)}}
                with mock.patch.object(hook_cli, "_read_stdin_bounded",
                                       return_value=json.dumps(payload)):
                    hook_cli.dirty_track_main([])
            self.assertEqual(["src/a.py"], WS.working_set(d, "rA").touched_files)
            self.assertEqual(["src/b.py"], WS.working_set(d, "rB").touched_files)

    def test_notes_are_trimmed_capped_and_refuse_an_empty_note(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            self.assertEqual("one two", WS.add_note(d, "rA", "  one   two "))
            with self.assertRaises(ValueError):
                WS.add_note(d, "rA", "   ")
            with mock.patch.object(WS, "NOTES_CAP", 2):
                for n in ("a", "b", "c"):
                    WS.add_note(d, "rA", n)
            self.assertEqual(["b", "c"], WS.working_set(d, "rA").notes)

    def test_the_mcp_tool_and_the_cli_say_the_same_thing(self):
        from mokata.mcp.tools_read import working_set
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            WS.record_touched(d, "rA", "src/a.py")
            out = working_set(path=d, run="rA", note="pick up the parser next")
            self.assertEqual("ok", out["status"])
            self.assertEqual(["src/a.py"], out["touched_files"])
            self.assertEqual(["pick up the parser next"], out["notes"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(0, main(["progress", "working-set", "--run", "rA",
                                          "--path", d]))
            self.assertIn(WS.working_set(d, "rA").line(), buf.getvalue())

    def test_the_re_anchor_block_carries_the_working_set(self):
        from mokata.reanchor import build_reanchor
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            WS.record_touched(d, "rA", "src/a.py")
            self.assertIn("working set: 1 file(s) touched: src/a.py",
                          build_reanchor(d, "S1", "compact"))

    def test_the_session_bundle_carries_the_extras(self):
        from mokata.session_bundle import build_session_bundle
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, "rA")
            WS.record_touched(d, "rA", "src/a.py")
            bundle = build_session_bundle(surface, run_id="rA", now="2026-10-09T00:00:00Z")
            self.assertEqual(["src/a.py"],
                             bundle["state"][WS.WORKING_SET_PREFIX + "rA"]["touched_files"])




class VerificationD5aOnlyRepoFilesAreRecorded(unittest.TestCase):

    def test_outside_paths_and_control_characters_are_not_recorded(self):
        import tempfile as _tf
        from mokata.init import init_repo as _init
        from mokata.working_set import record_touched, working_set
        with _tf.TemporaryDirectory() as d:
            _init(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            for p in ("../../etc/hosts", "/etc/passwd", "src/a\nb.py", "C:/x.py", "src/ok.py"):
                record_touched(d, "rA", p)
            self.assertEqual(["src/ok.py"], working_set(d, "rA").touched_files)


if __name__ == "__main__":
    unittest.main()
