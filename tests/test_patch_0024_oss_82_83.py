"""0.0.24 patch — the mokata parts of OSS #82 / #83 (Windows, 0.0.22), pinned on every OS.

* #82 item 1 — `baseline` over MCP could never finish a suite slower than 120 s. 0.0.24's
  `settings.mcp.test_timeout_seconds` budget was deleted in 0.0.29 lane G (#82-1b): no wall clock
  ends a suite; its pins live in `test_g_mcp_long_suite`. `spec_check`'s 60 s timeout was the 0.0.22
  per-symbol corpus walk, fixed in 0.0.23 (B1b).
* #82 item 3 — `mokata gate override` in an agent's shell (CLAUDECODE=1) refuses up front and names
  `--yes`, instead of asking a question nobody can answer (Windows shells report a TTY there).
* #82 item 4 — `spec_emit` with `proposal_id` ALONE redeems the approved proposal; the content hash
  still proves it is exactly what the human approved, and a tampered copy is refused. (0.0.28 round 3
  ruling: kept. The four spec fields are required only when no `proposal_id` is given.)
* #82 item 5 / #83 — the "local state persist degraded" path on Windows was `os.replace` losing to a
  concurrent reader; the retry is 0.0.23's `ATOMIC-REPLACE-WINDOWS` (test_cut_0023_windows_legs).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)
from _plan import (call_planned, commit_planned, emit_planned,  # noqa: F401,E402  (R2)
                   planned, sealed, with_tasks)

from mokata.config import Surface
from mokata.init import init_repo
from mokata.prompt import ASKED, ConsentDecision


def _repo():
    d = tempfile.TemporaryDirectory()
    init_repo(root=d.name, profile="minimal", assume_yes=True, out=lambda *_a: None)
    return d


class TheOverrideRefusesUpFrontInAnAgentShell(unittest.TestCase):

    def _run(self, env, yes):
        from mokata.cli_commands import gate as G
        args = argparse.Namespace(gate="no-code-without-failing-test", reason="because", yes=yes,
                                  actor="agent", path=".")
        run = mock.Mock(ambiguous=False, candidates=[])
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(G, "_resolve", return_value=(mock.Mock(), "run-1234", run)), \
                mock.patch("mokata.prompt.read_yes_no",
                           return_value=ConsentDecision(False, ASKED)) as ask, \
                redirect_stdout(out):
            rc = G.cmd_gate_override(args)
        return rc, ask, out.getvalue()

    def test_claudecode_without_yes_never_asks_and_names_the_flag(self):
        rc, ask, out = self._run({"CLAUDECODE": "1"}, yes=False)
        self.assertEqual(rc, 1)
        ask.assert_not_called()
        self.assertIn("--yes", out)

    def test_the_app_terminal_panel_is_asked_not_told_to_use_yes(self):
        # 0.0.29 H-a (Jas, 2026-11-16 "Drop"): the Claude app's Terminal panel sets its own variable
        # for the HUMAN's shell, without CLAUDECODE — the question is asked there, no `--yes` demanded.
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        env["CLAUDE_CODE_" + "ENTRYPOINT"] = "cli"
        with mock.patch.dict(os.environ, env, clear=True):
            rc, ask, out = self._run({}, yes=False)
        ask.assert_called_once()
        self.assertNotIn("agent's shell", out)

    def test_outside_an_agent_shell_the_question_is_still_asked(self):
        from mokata.prompt import AGENT_SHELL_SIGNALS      # 0.0.29 H-a round 3: one definition
        env = {k: v for k, v in os.environ.items() if k not in AGENT_SHELL_SIGNALS}
        with mock.patch.dict(os.environ, env, clear=True):
            rc, ask, _out = self._run({}, yes=False)
        ask.assert_called_once()


class SpecEmitRedeemsWithTheIdAlone(unittest.TestCase):
    """Reuses the real round-trip harness from test_si_dev_0_spec_emit.

    0.0.28 round 3 (coordinator ruling under Jas's delegation): the shipped 0.0.24 behaviour stays.
    Forcing a model to re-send a thousands-of-tokens payload byte-for-byte is the fail-then-fix loop
    lane I exists to remove; the saved copy IS what the human approved, so P2 holds."""

    def setUp(self):
        import test_si_dev_0_spec_emit as H
        self.H = H

    def _propose_and_approve(self, repo):
        from mokata import approval
        from mokata.govern.ledger import AuditLedger
        from mokata.mcp import tools_write as TW
        proposed = call_planned(TW.spec_emit, **self.H._args(repo.path))
        self.assertEqual(proposed["status"], "proposed")
        pid = proposed["proposal_id"]
        ledger = AuditLedger.from_mokata_dir(os.path.join(repo.path, ".mokata"))
        approval.approve(repo.path, pid, actor="jas", ledger=ledger)
        return TW, pid

    def test_proposal_id_alone_commits_exactly_once(self):
        with self.H._Repo() as repo:
            TW, pid = self._propose_and_approve(repo)
            res = call_planned(TW.spec_emit, path=repo.path, proposal_id=pid)
            self.assertTrue(res.get("committed"), res)
            again = call_planned(TW.spec_emit, path=repo.path, proposal_id=pid)
            self.assertFalse(again.get("committed"), "an approval licenses exactly one write")

    def test_a_tampered_saved_payload_is_refused_by_the_hash(self):
        from mokata.mcp import tools_spec
        with self.H._Repo() as repo:
            TW, pid = self._propose_and_approve(repo)
            state = Surface.load(repo.path).state
            saved = state.read(tools_spec.EMIT_ARGS_PREFIX + pid)
            saved["title"] = "something the human never saw"
            state.write(tools_spec.EMIT_ARGS_PREFIX + pid, saved)
            res = call_planned(TW.spec_emit, path=repo.path, proposal_id=pid)
            self.assertFalse(res.get("committed"), res)

    def test_drifted_content_with_the_id_is_refused_as_content_changed(self):
        from mokata import approval
        with self.H._Repo() as repo:
            TW, pid = self._propose_and_approve(repo)
            drifted = dict(self.H._args(repo.path), title="a title the human never approved")
            res = call_planned(TW.spec_emit, **dict(drifted, proposal_id=pid))
            self.assertFalse(res.get("committed"), res)
            self.assertEqual(approval.REFUSED_CONTENT_CHANGED, res.get("reason_code"), res)

    def test_the_full_payload_still_works(self):
        with self.H._Repo() as repo:
            TW, pid = self._propose_and_approve(repo)
            res = call_planned(TW.spec_emit, **dict(self.H._args(repo.path), proposal_id=pid))
            self.assertTrue(res.get("committed"), res)

    def test_an_unknown_id_alone_is_refused_naming_what_to_send(self):
        # 0.0.28 I2: a caller fault is a `ValidationError` (the front door's `refused`), not a
        # hand-built `status:"error"`; the reason still names what to send.
        from mokata.mcp import tools_write as TW
        from mokata.mcp.validation import ValidationError
        with self.H._Repo() as repo:
            with self.assertRaises(ValidationError) as cm:
                call_planned(TW.spec_emit, path=repo.path, proposal_id="nope")
            self.assertEqual("proposal_id", cm.exception.field)
            for name in ("title", "criteria", "tests", "tasks"):
                self.assertIn(name, cm.exception.reason)

    def test_no_id_and_missing_fields_is_a_strict_refusal_naming_them(self):
        from mokata.mcp import tools_write as TW
        from mokata.mcp.validation import ValidationError
        with self.H._Repo() as repo:
            with self.assertRaises(ValidationError) as cm:
                TW.spec_emit(path=repo.path, title="only a title")
            self.assertEqual("criteria", cm.exception.field)
            for name in ("criteria", "tests", "tasks"):
                self.assertIn(name, cm.exception.reason)
            self.assertEqual({"criteria", "tests", "tasks"}, set(cm.exception.retry_with))


class ADegradedSaveNamesItsCause(unittest.TestCase):
    """#82 item 5 / #83 — the MCP client sees the error TYPE and code, never only "see the warning"."""

    def test_a_permission_error_is_named_with_its_code_and_the_disk_remedy(self):
        # 0.0.29 lane G (#83): a save that raised before any write is `ok:false` and says NOTHING
        # was saved; an OSError gets the disk remedy (re-call, then doctor). The 0.0.24 "upgrade"
        # sentence is deleted (the Windows retry it pointed at shipped in 0.0.23).
        from mokata.mcp import tools_read
        d = _repo()
        self.addCleanup(d.cleanup)
        boom = PermissionError(13, "Access is denied: C:\\secret\\path")
        with mock.patch("mokata.session_flow.save_session", side_effect=boom), \
                mock.patch("mokata.session_flow.note_persist_failure"):
            out = tools_read.session_save(d.name, brainstorm={"topic": "x"})
        self.assertIs(False, out["ok"])
        self.assertEqual("degraded", out["status"])
        self.assertTrue(out["error"].startswith("PermissionError ["), out["error"])
        self.assertIn("nothing was saved", out["message"])
        self.assertIn("mokata doctor", out["message"])
        self.assertNotIn("upgrade", out["message"])
        self.assertNotIn("secret", json.dumps(out), "the exception's text must never be echoed")


if __name__ == "__main__":
    unittest.main()
