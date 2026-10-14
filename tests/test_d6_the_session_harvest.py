"""0.0.22 stage D6 — `episodic-harvest`: H-1c (+ H-1b's episodic half).

A session's work is drafted, from mokata's RECORD, as one note per session at Stop / PreCompact /
SessionEnd. A draft is NOT a memory: it becomes an `episodic` memory only when a person accepts it
through the store's human-gated `remember`. A decided draft does not come back unless the session
did more. Only sessions in a pipeline are harvested; parallel sessions each get their own.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harvest as H
from mokata import hook_cli
from mokata import run_intent as RI
from mokata import working_set as WS
from mokata.cli import main
from mokata.config import Surface
from mokata.govern.resume import PipelineCheckpoint
from mokata.init import init_repo


def _silent(_):
    pass


def _repo(d, *runs):
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    surface = Surface.load(d)
    for rid in runs:
        PipelineCheckpoint(surface.state, rid).ensure_registered()
    return Surface.load(d)


def _hook(fn, payload):
    out = io.StringIO()
    with mock.patch.object(hook_cli, "_read_stdin_bounded", return_value=json.dumps(payload)), \
            redirect_stdout(out):
        code = fn([])
    return code, out.getvalue()


def _memories(d):
    from mokata.memory.item import EPISODIC
    from mokata.memory.store import MemoryStore
    return [i for i in MemoryStore.from_surface(Surface.load(d)).backend.all()
            if i.mtype == EPISODIC]


class TheDraftIsBuiltFromTheRecord(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def test_stop_drafts_the_sessions_work_silently_and_writes_no_memory(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            WS.record_touched(d, "rA", "src/app.py")
            WS.add_note(d, "rA", "parser next")
            code, out = _hook(hook_cli.stop_main, {
                "cwd": d, "session_id": "S1", "hook_event_name": "Stop",
                "last_assistant_message": "Done: the parser handles empty input now."})
            self.assertEqual((0, ""), (code, out), "a Stop hook must be silent and exit 0")
            [draft] = H.drafts(d)
            self.assertEqual(("S1", "rA"), (draft.id, draft.run_id))
            for fact in ("src/app.py", "parser next", "no stage entered",
                         "the parser handles empty input"):
                self.assertIn(fact, draft.value)
            self.assertEqual([], _memories(d), "a draft is not a memory")

    def test_the_note_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            for i in range(60):
                WS.record_touched(d, "rA", f"src/m{i:02d}/very_long_module_name_{i}.py")
            note = H.build_note(d, "rA", "x" * 5000)
            self.assertLessEqual(len(note), H.MAX_NOTE_CHARS)
            self.assertIn("more)", note)

    def test_precompact_and_session_end_draft_too_and_keep_the_last_word(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            RI.bind_registered(d, "S1", "rA")
            _hook(hook_cli.stop_main, {"cwd": d, "session_id": "S1",
                                       "last_assistant_message": "Halfway through."})
            WS.record_touched(d, "rA", "src/b.py")
            _hook(hook_cli.session_end_main, {"cwd": d, "session_id": "S1", "reason": "clear"})
            [draft] = H.drafts(d)
            self.assertIn("src/b.py", draft.value)
            self.assertIn("Halfway through.", draft.value, "SessionEnd carries no message")
            _hook(hook_cli.pre_compact_main, {"cwd": d, "session_id": "S1"})
            self.assertEqual(1, len(H.drafts(d)), "one draft per session, rewritten")

    def test_a_session_in_no_pipeline_is_not_harvested(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA")
            _hook(hook_cli.stop_main, {"cwd": d, "session_id": "S9"})
            self.assertEqual([], H.drafts(d))

    def test_parallel_sessions_each_draft_their_own_run(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d, "rA", "rB")
            RI.bind_registered(d, "S1", "rA")
            RI.bind_registered(d, "S2", "rB")
            WS.record_touched(d, "rA", "src/a.py")
            WS.record_touched(d, "rB", "src/b.py")
            for sid in ("S1", "S2"):
                _hook(hook_cli.stop_main, {"cwd": d, "session_id": sid})
            by = {x.id: x for x in H.drafts(d)}
            self.assertIn("src/a.py", by["S1"].value)
            self.assertNotIn("src/b.py", by["S1"].value)
            self.assertIn("src/b.py", by["S2"].value)


class APersonDecides(unittest.TestCase):

    def setUp(self):
        os.environ.pop("MOKATA_SESSION_ID", None)

    def _draft(self, d):
        _repo(d, "rA")
        RI.bind_registered(d, "S1", "rA")
        WS.record_touched(d, "rA", "src/app.py")
        _hook(hook_cli.stop_main, {"cwd": d, "session_id": "S1"})

    def test_accept_saves_an_episodic_memory_through_the_human_gate(self):
        with tempfile.TemporaryDirectory() as d:
            self._draft(d)
            self.assertIn("not saved", H.decide(d, "S1", True, confirm=lambda _: False),
                          "the human said no — nothing is saved")
            self.assertEqual([], _memories(d))
            msg = H.decide(d, "S1", True, confirm=lambda _: True)
            self.assertIn("saved as episodic memory", msg)
            [mem] = _memories(d)
            self.assertIn("src/app.py", mem.value)
            self.assertEqual("harvest", mem.provenance.get("source"))
            self.assertEqual([], H.drafts(d), "no longer pending")

    def test_a_decided_draft_is_not_proposed_again_unless_the_session_did_more(self):
        with tempfile.TemporaryDirectory() as d:
            self._draft(d)
            H.decide(d, "S1", False)
            _hook(hook_cli.stop_main, {"cwd": d, "session_id": "S1"})
            self.assertEqual([], H.drafts(d), "same content, already decided")
            WS.record_touched(d, "rA", "src/more.py")
            _hook(hook_cli.stop_main, {"cwd": d, "session_id": "S1"})
            self.assertEqual(1, len(H.drafts(d)), "new work is a new proposal")

    def test_a_new_chat_message_alone_is_not_new_work(self):
        """0.0.22 verification — the digest keyed on the model's last message, so a decided draft
        came back after any chat turn ("Thanks, anything else?")."""
        with tempfile.TemporaryDirectory() as d:
            self._draft(d)
            H.decide(d, "S1", False)
            _hook(hook_cli.stop_main, {"cwd": d, "session_id": "S1",
                                       "last_assistant_message": "Thanks, anything else?"})
            self.assertEqual([], H.drafts(d), "a chat turn is not new work")

    def test_an_unknown_or_decided_id_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self._draft(d)
            H.decide(d, "S1", False)
            for bad in ("S1", "nope", "../x"):
                with self.subTest(bad=bad), self.assertRaises(H.HarvestError):
                    H.decide(d, bad, True)

    def test_the_cli_lists_and_decides(self):
        with tempfile.TemporaryDirectory() as d:
            self._draft(d)
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(0, main(["memory", "harvest", "--path", d]))
            self.assertIn("[S1]", buf.getvalue())
            self.assertIn("src/app.py", buf.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["memory", "harvest", "--approve", "S1", "--yes",
                                          "--path", d]))
            self.assertEqual(1, len(_memories(d)))

    def test_the_briefing_says_drafts_are_waiting_inside_the_budget(self):
        from mokata.bootstrap import estimate_tokens
        from mokata.context_budget import ContextBudget
        with tempfile.TemporaryDirectory() as d:
            self._draft(d)
            buf = io.StringIO()
            with mock.patch.object(hook_cli, "_payload_from_stdin",
                                   return_value={"cwd": d, "session_id": "S2"}), \
                    redirect_stdout(buf):
                hook_cli.session_start_main([])
            ctx = json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]
            self.assertIn("1 session note(s)", ctx)
            self.assertLessEqual(estimate_tokens(ctx), ContextBudget().briefing_tokens)


class TheHooksAreWired(unittest.TestCase):

    def test_stop_and_session_end_are_wired(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "src", "mokata", "hooks", "hooks.json"),
                  encoding="utf-8") as fh:
            hooks = json.load(fh)["hooks"]
        self.assertIn("mokata-hook-launch\\\" stop", json.dumps(hooks["Stop"]))
        self.assertIn("session-end", json.dumps(hooks["SessionEnd"]))


if __name__ == "__main__":
    unittest.main()
