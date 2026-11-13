"""The D7 live-check grader reads every event shape `claude -p --output-format stream-json` emits.

Jas's first run (2026-11-09) died on an event whose `message` is a string; the grader must skip
such events and still count the mokata calls around them.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (the provisioning preflight every test module runs)

_HERE = os.path.dirname(os.path.abspath(__file__))
_PATH = os.path.join(os.path.dirname(_HERE), "scripts", "mcp_live_check.py")


def _load():
    spec = importlib.util.spec_from_file_location("mcp_live_check", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ev(**kw):
    return json.dumps(kw)


class TheGraderSurvivesEveryEventShape(unittest.TestCase):
    def setUp(self):
        self.grade = _load()._grade_stream

    def test_string_message_and_non_object_events_are_skipped_and_calls_still_counted(self):
        lines = [
            _ev(type="system", message="init: mcp servers connecting"),
            json.dumps(["not", "an", "object"]),
            json.dumps("bare string"),
            _ev(type="assistant", message={"content": "plain text content"}),
            _ev(type="assistant", message={"content": [
                {"type": "tool_use", "id": "t1", "name": "mcp__mokata__status"}]}),
            _ev(type="error", message="transient"),
            _ev(type="user", message={"content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": [{"type": "text", "text": '{"ok": true}'}]}]}),
            "not json at all",
        ]
        g = self.grade(lines)
        self.assertEqual([c["tool"] for c in g["calls"]], ["status"])
        self.assertTrue(g["first_ok"])

    def test_an_invalid_input_first_call_is_not_first_ok(self):
        lines = [
            _ev(type="assistant", message={"content": [
                {"type": "tool_use", "id": "t1", "name": "mcp__mokata__recall"}]}),
            _ev(type="user", message={"content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                 "content": '{"error": "invalid_input"}'}]}),
        ]
        g = self.grade(lines)
        self.assertEqual(len(g["calls"]), 1)
        self.assertFalse(g["first_ok"])

    def test_a_failed_session_is_reported_as_the_session_never_as_zero_calls(self):
        lines = [
            _ev(type="system", subtype="init", mcp_servers=[{"name": "mokata", "status": "connected"}]),
            _ev(type="assistant", message={"content": [
                {"type": "text", "text": "Failed to authenticate: OAuth session expired"}]}),
            _ev(type="result", subtype="success", is_error=True,
                result="Failed to authenticate: OAuth session expired and could not be refreshed"),
        ]
        g = self.grade(lines)
        self.assertEqual(g["calls"], [])
        self.assertIn("OAuth session expired", g["session_error"])
        self.assertFalse(g["first_ok"])

    def test_a_good_session_has_no_session_error(self):
        lines = [_ev(type="result", subtype="success", is_error=False, result="done")]
        self.assertIsNone(self.grade(lines)["session_error"])


class MainRefusesToGradeNothing(unittest.TestCase):
    """Jas's 2026-11-09 run printed first_call_success=0/0 and exited 0 on an expired login:
    a check that cannot run must say so and exit non-zero, never look like a pass."""

    def _main_with(self, stream):
        mod = _load()
        mod._fixture = lambda src, init: tempfile.mkdtemp(prefix="mlc-test-")
        mod._mcp_config = lambda src, repo: os.path.join(repo, "cfg.json")
        mod._mcp_logs_since = lambda started: (0, 0, [])
        mod._refuse_api_key = lambda: None
        ran = []

        def fake_run(claude, model, repo, cfg, prompt, timeout):
            ran.append(prompt)
            return stream
        mod._run = fake_run
        out = io.StringIO()
        # Scoped: `mod.shutil` IS the process-wide shutil module, so assigning to it would make
        # every later test's which() answer "/bin/true" (it broke 44 tests in the full suite).
        with mock.patch.object(mod.shutil, "which", lambda name, *a, **k: sys.executable), \
                contextlib.redirect_stdout(out):
            rc = mod.main([])
        return rc, out.getvalue(), ran

    def test_an_expired_login_stops_at_the_first_prompt_with_rc_2(self):
        rc, out, ran = self._main_with([_ev(type="result", is_error=True,
                                            result="Failed to authenticate: OAuth session expired")])
        self.assertEqual(rc, 2)
        self.assertEqual(len(ran), 1, "it must not spend eleven more prompts on a dead login")
        self.assertIn("NOTHING GRADED", out)
        self.assertIn("OAuth session expired", out)

    def test_no_mokata_call_in_any_prompt_is_rc_2(self):
        rc, out, ran = self._main_with([_ev(type="result", is_error=False, result="I did it myself")])
        self.assertEqual(rc, 2)
        self.assertEqual(len(ran), 12)
        self.assertIn("NOTHING GRADED", out)

    def test_the_fake_which_does_not_outlive_the_test(self):
        real = shutil.which
        self._main_with([_ev(type="result", is_error=False, result="x")])
        self.assertIs(shutil.which, real, "a leaked which() poisons every later test in the process")


if __name__ == "__main__":
    unittest.main()
