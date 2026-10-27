"""0.0.24 patch — the mokata parts of OSS #82 / #83 (Windows, 0.0.22), pinned on every OS.

* #82 item 1 — `baseline` over MCP could never finish a suite slower than 120 s. The budget is now
  `settings.mcp.test_timeout_seconds` (clamped to the 600 s terminal bound), and the timeout hint
  names the setting. `spec_check`'s 60 s timeout was the 0.0.22 per-symbol corpus walk, fixed in
  0.0.23 (B1b); it keeps the interactive budget.
* #82 item 3 — `mokata gate override` in an agent's shell (CLAUDECODE=1) refuses up front and names
  `--yes`, instead of asking a question nobody can answer (Windows shells report a TTY there).
* #82 item 4 — `spec_emit` with `proposal_id` ALONE redeems the approved proposal; the content hash
  still proves it is exactly what the human approved, and a tampered copy is refused.
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

from mokata import baseline as B
from mokata.config import Surface
from mokata.init import init_repo
from mokata.mcp import server as MS
from mokata.prompt import ASKED, ConsentDecision


def _repo():
    d = tempfile.TemporaryDirectory()
    init_repo(root=d.name, profile="minimal", assume_yes=True, out=lambda *_a: None)
    return d


def _set(root, value):
    path = os.path.join(root, ".mokata", "manifest.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("settings", {})["mcp"] = {"test_timeout_seconds": value}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


class TheBaselineBudgetIsTheRepos(unittest.TestCase):

    def setUp(self):
        self.d = _repo()
        self.addCleanup(self.d.cleanup)
        self.root = self.d.name

    def test_the_default_is_unchanged(self):
        self.assertEqual(MS._mcp_timeout_for("baseline", self.root), B.BASELINE_MCP_TIMEOUT_SECONDS)

    def test_the_setting_raises_it_as_config_set_stores_it(self):
        _set(self.root, 300)
        self.assertEqual(MS._mcp_timeout_for("baseline", self.root), 300.0)
        self.assertEqual(B.mcp_test_timeout(Surface.load(self.root).manifest), 300.0)

    def test_it_never_exceeds_the_terminal_bound(self):
        _set(self.root, 99999)
        self.assertEqual(MS._mcp_timeout_for("baseline", self.root),
                         float(B.BASELINE_TIMEOUT_SECONDS))

    def test_a_bad_value_is_the_default_never_an_error(self):
        for bad in ("soon", -5, 0, True, None):
            _set(self.root, bad)
            self.assertEqual(MS._mcp_timeout_for("baseline", self.root),
                             float(B.BASELINE_MCP_TIMEOUT_SECONDS), bad)

    def test_other_tools_keep_the_interactive_budget(self):
        _set(self.root, 300)
        self.assertEqual(MS._mcp_timeout_for("spec_check", self.root),
                         MS.MCP_SURFACE_TIMEOUT_SECONDS)

    def test_the_timeout_hint_names_the_setting_for_baseline_only(self):
        self.assertIn("settings.mcp.test_timeout_seconds",
                      MS._still_running("baseline", 120, 121, "c")["hint"])
        self.assertNotIn("test_timeout_seconds",
                         MS._still_running("spec_check", 60, 61, "c")["hint"])

    def test_the_mcp_baseline_tool_hands_the_subprocess_the_same_budget(self):
        _set(self.root, 300)
        from mokata.mcp import tools_read
        seen = {}

        def fake(cmd, cwd, timeout, **_kw):
            seen["timeout"] = timeout
            return B.BaselineResult(state=B.GREEN, command=cmd or "x", returncode=0, detail="")
        with mock.patch.object(B, "baseline_status", side_effect=fake):
            tools_read.baseline(self.root, cmd="true")
        self.assertEqual(seen["timeout"], 300.0)


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

    def test_outside_an_agent_shell_the_question_is_still_asked(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        with mock.patch.dict(os.environ, env, clear=True):
            rc, ask, _out = self._run({}, yes=False)
        ask.assert_called_once()


class SpecEmitRedeemsWithTheIdAlone(unittest.TestCase):
    """Reuses the real round-trip harness from test_si_dev_0_spec_emit."""

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

    def test_an_unknown_id_says_what_to_send(self):
        from mokata.mcp import tools_write as TW
        with self.H._Repo() as repo:
            res = call_planned(TW.spec_emit, path=repo.path, proposal_id="nope")
            self.assertEqual(res["status"], "error")
            self.assertIn("title + criteria + tests", res["hint"])

    def test_the_full_payload_still_works(self):
        with self.H._Repo() as repo:
            TW, pid = self._propose_and_approve(repo)
            res = call_planned(TW.spec_emit, **dict(self.H._args(repo.path), proposal_id=pid))
            self.assertTrue(res.get("committed"), res)


class ADegradedSaveNamesItsCause(unittest.TestCase):
    """#82 item 5 / #83 — the MCP client sees the error TYPE and code, never only "see the warning"."""

    def test_a_permission_error_is_named_with_its_code_and_the_upgrade_hint(self):
        from mokata.mcp import tools_read
        d = _repo()
        self.addCleanup(d.cleanup)
        boom = PermissionError(13, "Access is denied: C:\\secret\\path")
        with mock.patch("mokata.session_flow.save_session", side_effect=boom), \
                mock.patch("mokata.session_flow.note_persist_failure"):
            out = tools_read.session_save(d.name, brainstorm={"topic": "x"})
        self.assertTrue(out["degraded"])
        self.assertTrue(out["error"].startswith("PermissionError ["), out["error"])
        self.assertIn("upgrade", out["message"])
        self.assertNotIn("secret", json.dumps(out), "the exception's text must never be echoed")


if __name__ == "__main__":
    unittest.main()
