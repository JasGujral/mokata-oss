"""0.0.25 Lane HB — U8: opt-in, fail-closed `http` gate hooks (`onFailure: "block"`) whose verdict IS
mokata's own gate code.

Business rules — what must be true for the user:
  * OFF by default. `mokata setup claude` alone wires no http gate; `--http-gates` wires ONE
    PreToolUse http hook with `onFailure: "block"` (Claude Code ≥ 2.1.295, the schema verified against
    code.claude.com/docs/en/hooks) into the machine-local `.claude/settings.local.json`; a setup run
    without the flag removes it; unsetup removes it; the user's own entries survive every step.
  * The `mokata-hook` command gates stay wired in `settings.json` either way — the http lane is an
    addition, never a replacement.
  * No second policy: the endpoint's verdict for a payload is exactly what the two command hooks
    decide for the same payload (block iff either exits 2, with their own words).
  * The door is the ID.S3 hook door: the hook token, no Origin, the Host allowlist.
  * The plan says, before the human says yes, that every gated tool call is BLOCKED while the IDE is
    not running; `mokata doctor` says it while the flag is on, and says it as an ERROR when the
    server is down.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup as HS
from mokata import hook_cli
from mokata.govern.doctor import http_gate_findings
from mokata.ide import bus as B
from mokata.ide import gate_door as G
from mokata.init import init_repo
from mokata.quiet_spawn import hidden

SRC = str(Path(__file__).resolve().parents[1] / "src")
# A deliberately fake, well-formed credential (the secret-guard fixtures' shape) — never a real key.
SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"


def _silent(*_a, **_k):
    pass


def _repo(d: str) -> None:
    init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    with open(os.path.join(d, "src", "a.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")


def _pre(d: str, tool: str, tool_input: dict, sid: str = "S1") -> dict:
    return {"session_id": sid, "cwd": d, "hook_event_name": "PreToolUse", "tool_name": tool,
            "transcript_path": "/dev/null", "permission_mode": "default", "tool_input": tool_input}


def _command_hooks(d: str, payload: dict):
    """What the two COMMAND gates decide for `payload`, as Claude Code runs them: each in its own
    process, only when its matcher covers the tool; blocked iff either exits 2."""
    import re
    env = dict(os.environ, PYTHONPATH=SRC)
    code, words = 0, []
    for sub, matcher in (("secret-guard", HS.HOOK_PRETOOL_MATCHER),
                         ("gate-guard", HS.HOOK_GATE_MATCHER)):
        if not re.fullmatch(matcher, payload["tool_name"]):
            continue
        p = subprocess.run([sys.executable, "-m", "mokata.hook_cli", sub], input=json.dumps(payload),
                           cwd=d, env=env, text=True, encoding="utf-8", capture_output=True,
                           timeout=60, **hidden())
        if p.returncode == 2:
            code = 2
            words.append(p.stderr.strip())
    return code, "\n".join(w for w in words if w)


# ======================================================================================
# the verdict IS the command gates' verdict
# ======================================================================================

class TheHttpVerdictIsTheCommandGatesVerdict(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.d = os.path.realpath(cls._t.name)
        subprocess.run(["git", "init", "-q", cls.d], check=True, capture_output=True,
                       stdin=subprocess.DEVNULL, **hidden())
        _repo(cls.d)
        from mokata import run_intent as RI
        from mokata.config import Surface
        from mokata.govern.resume import PipelineCheckpoint
        PipelineCheckpoint(Surface.load(cls.d).state, "r1").ensure_registered()
        RI.bind_registered(cls.d, "S2", "r1")       # S2: a bound run with no approach yet

    @classmethod
    def tearDownClass(cls):
        cls._t.cleanup()

    def _cases(self):
        d = self.d
        site = os.path.join(os.path.dirname(os.__file__), "site-packages", "x.py")
        return {
            "a benign write": _pre(d, "Write", {"file_path": os.path.join(d, "src", "a.py"),
                                                "content": "x = 2\n"}),
            "a secret in a write": _pre(d, "Write", {"file_path": os.path.join(d, "src", "k.py"),
                                                     "content": f"KEY = '{SECRET}'\n"}),
            "a secret in a Bash command": _pre(d, "Bash", {"command": f"echo {SECRET} > k"}),
            "a write into installed code (self-protect)": _pre(d, "Write", {
                "file_path": site, "content": "x = 1\n"}),
            "a run-state gate block (bound run, no approach)": _pre(d, "Write", {
                "file_path": os.path.join(d, "src", "impl.py"), "content": "y = 1\n"}, sid="S2"),
            "a tool only gate-guard matches": _pre(d, "NotebookEdit", {
                "notebook_path": os.path.join(d, "n.ipynb"), "new_source": "1"}),
            "a tool neither gate matches": _pre(d, "Read", {"file_path": os.path.join(d, "a")}),
        }

    def test_every_case_gets_the_same_verdict_and_words_as_the_command_hooks(self):
        blocked = 0
        for label, payload in self._cases().items():
            want = _command_hooks(self.d, payload)
            got = hook_cli.pretooluse_verdict(payload)
            self.assertEqual(want, got, label)
            blocked += got[0] == 2
        self.assertGreaterEqual(blocked, 4, "the fixture really exercises blocks")

    def test_the_verdict_never_touches_the_processes_own_stdin_or_stderr(self):
        payload = self._cases()["a secret in a write"]
        fake_in, fake_err = io.StringIO("UNTOUCHED"), io.StringIO()
        with mock.patch.object(sys, "stdin", fake_in), mock.patch.object(sys, "stderr", fake_err):
            code, words = hook_cli.pretooluse_verdict(payload)
        self.assertEqual(2, code)
        self.assertTrue(words)
        self.assertEqual("UNTOUCHED", fake_in.read())
        self.assertNotIn(SECRET[-6:], fake_err.getvalue())

    def test_concurrent_verdicts_never_share_a_buffer(self):
        cases = self._cases()
        want = {k: hook_cli.pretooluse_verdict(v) for k, v in cases.items()}
        got, errors = {}, []

        def run(label):
            try:
                for _ in range(3):
                    got.setdefault(label, set()).add(hook_cli.pretooluse_verdict(cases[label]))
            except Exception as exc:  # noqa: BLE001 — surfaced by the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(k,)) for k in cases]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        self.assertEqual([], errors)
        for label, results in got.items():
            self.assertEqual({want[label]}, results, label)


# ======================================================================================
# the door: mapping, auth, live answer
# ======================================================================================

class TheGateDoorAnswersLikeAClaudeCodeHttpHookExpects(unittest.TestCase):

    def test_allow_is_an_empty_2xx_and_block_is_a_deny_decision(self):
        self.assertEqual((200, None), G.http_answer((0, "")))
        status, body = G.http_answer((2, "BLOCKED [x] because"))
        self.assertEqual(200, status)
        self.assertEqual({"hookEventName": "PreToolUse", "permissionDecision": "deny",
                          "permissionDecisionReason": "BLOCKED [x] because"},
                         body["hookSpecificOutput"])

    def test_an_unreadable_call_is_never_a_yes(self):
        for raw in (b"", b"not json", b"[1,2]", b"\xff\xfe"):
            self.assertEqual(400, G.decide(raw)[0], raw)

    def test_the_route_runs_the_gates_behind_the_hook_door(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            _repo(d)
            rec = B.ensure_bridge(d)
            state = AppState(d, "pagetok", 9999, None, False)
            hook = {B.HOOK_HEADER: rec["token"]}
            with TestClient(build_app(state), base_url="http://127.0.0.1:9999") as c:
                ok = c.post(G.GATE_PATH, headers=hook, json=_pre(d, "Write", {
                    "file_path": os.path.join(d, "src", "a.py"), "content": "x = 3\n"}))
                self.assertEqual((200, b""), (ok.status_code, ok.content))
                no = c.post(G.GATE_PATH, headers=hook, json=_pre(d, "Write", {
                    "file_path": os.path.join(d, "src", "k.py"), "content": f"K = '{SECRET}'\n"}))
                self.assertEqual(200, no.status_code)
                self.assertEqual("deny", no.json()["hookSpecificOutput"]["permissionDecision"])
                self.assertEqual(400, c.post(G.GATE_PATH, headers=hook, content=b"x").status_code)
                self.assertEqual(403, c.post(G.GATE_PATH, json={}, headers={
                    **hook, "Origin": "http://evil.example"}).status_code)
                self.assertEqual(403, c.post(G.GATE_PATH, json={},
                                             headers={B.HOOK_HEADER: "wrong"}).status_code)
            with TestClient(build_app(state), base_url="http://evil.example:9999") as evil:
                self.assertEqual(403, evil.post(G.GATE_PATH, json={}, headers=hook).status_code)


# ======================================================================================
# setup: off by default, opt-in, removed when not asked for, reversible
# ======================================================================================

class SetupWiresTheHttpGatesOnlyWhenAsked(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        base = Path(self._t.name)
        self.root = base / "repo"
        self.root.mkdir()
        self.home = base / "home"
        self.home.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def _setup(self, **kw):
        plan = HS.plan_setup("claude", root=str(self.root), home=str(self.home), **kw)
        HS.apply_setup(plan, assume_yes=True, out=_silent)
        return plan

    def _local(self):
        p = self.root / ".claude" / "settings.local.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def test_off_by_default(self):
        self._setup()
        self.assertIsNone(self._local())
        self._setup(ide_bridge=True)
        self.assertNotIn("/api/gate", json.dumps(self._local()))

    def test_the_flag_writes_the_documented_fail_closed_hook(self):
        self._setup(http_gates=True)
        rec = B.read_bridge(str(self.root.resolve()))
        (group,) = self._local()["hooks"]["PreToolUse"]
        self.assertEqual("Write|Edit|MultiEdit|Bash|NotebookEdit|Skill|Agent|Task", group["matcher"])
        (h,) = group["hooks"]
        self.assertEqual({"type": "http", "url": f"http://127.0.0.1:{rec['port']}/api/gate",
                          "timeout": HS.HTTP_GATES_TIMEOUT_SECS,
                          "headers": {"X-Mokata-Hook": rec["token"]}, "onFailure": "block"}, h)
        shared = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn(rec["token"], json.dumps(shared))
        subs = [h["args"][0] for g in shared["hooks"]["PreToolUse"] for h in g["hooks"]
                if h.get("type") == "command"]
        self.assertEqual(["secret-guard", "gate-guard"], subs, "the command gates stay wired")

    def test_a_setup_without_the_flag_removes_it_and_keeps_everything_else(self):
        local = self.root / ".claude" / "settings.local.json"
        local.parent.mkdir(parents=True, exist_ok=True)
        mine = {"type": "command", "command": "echo mine"}
        local.write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [mine]}]},
                                     "env": {"K": "v"}}), encoding="utf-8")
        self._setup(http_gates=True, ide_bridge=True)
        self._setup(http_gates=True, ide_bridge=True)
        data = self._local()
        self.assertEqual(2, len(data["hooks"]["PreToolUse"]), "re-setup duplicated the gate")
        self.assertIn("/api/bus", json.dumps(data))
        text = HS.render_setup_plan(HS.plan_setup("claude", root=str(self.root),
                                                  home=str(self.home), ide_bridge=True))
        self.assertIn("Will REMOVE the fail-closed http gate hooks", text)
        self._setup(ide_bridge=True)
        data = self._local()
        self.assertNotIn("/api/gate", json.dumps(data))
        self.assertIn("/api/bus", json.dumps(data), "the bridge is not the gate's to remove")
        self.assertIn({"hooks": [mine]}, data["hooks"]["PreToolUse"])
        self._setup(http_gates=True)
        HS.apply_unsetup(HS.plan_unsetup("claude", root=str(self.root), home=str(self.home)))
        self.assertEqual({"hooks": {"PreToolUse": [{"hooks": [mine]}]}, "env": {"K": "v"}},
                         self._local())

    def test_the_plan_says_it_blocks_while_the_ide_is_down_before_anything_is_written(self):
        text = HS.render_setup_plan(HS.plan_setup("claude", root=str(self.root),
                                                  home=str(self.home), http_gates=True))
        self.assertIn("FAIL-CLOSED", text)
        self.assertIn("BLOCKS every", text)
        self.assertIn("mokata ide --foreground", text)
        self.assertIn("2.1.295", text)
        self.assertIsNone(self._local(), "rendering a plan wrote something")

    def test_user_scope_says_why_it_is_not_wired(self):
        plan = HS.plan_setup("claude", root=str(self.root), home=str(self.home), scope="user",
                             http_gates=True)
        self.assertFalse(plan.http_gates)
        self.assertIn("http gates not wired", HS.render_setup_plan(plan))

    def test_the_cli_flag_parses_and_is_off_by_default(self):
        from mokata.cli import build_parser
        p = build_parser()
        self.assertFalse(p.parse_args(["setup", "claude"]).http_gates)
        self.assertTrue(p.parse_args(["setup", "claude", "--http-gates"]).http_gates)


# ======================================================================================
# doctor
# ======================================================================================

class DoctorSaysWhatTheFlagCosts(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        base = Path(self._t.name)
        self.root = base / "repo"
        self.root.mkdir()
        self.home = base / "home"
        self.home.mkdir()
        self.surface = type("S", (), {"root": str(self.root)})()

    def tearDown(self):
        self._t.cleanup()

    def _codes(self, **kw):
        return {f.code: f.severity for f in http_gate_findings(self.surface, **kw)}

    def test_silent_when_not_wired(self):
        HS.apply_setup(HS.plan_setup("claude", root=str(self.root), home=str(self.home)),
                       assume_yes=True, out=_silent)
        self.assertEqual({}, self._codes(listening=lambda _p: False, version=(2, 1, 295)))

    def test_on_down_and_too_old(self):
        HS.apply_setup(HS.plan_setup("claude", root=str(self.root), home=str(self.home),
                                     http_gates=True), assume_yes=True, out=_silent)
        self.assertEqual({"http-gates-on": "info"},
                         self._codes(listening=lambda _p: True, version=(2, 1, 295)))
        self.assertEqual({"http-gates-on": "info", "http-gates-server-down": "error"},
                         self._codes(listening=lambda _p: False, version=(2, 1, 300)))
        self.assertEqual("warning", self._codes(listening=lambda _p: True, version=(2, 1, 294))
                         ["http-gates-claude-code-too-old"])


if __name__ == "__main__":
    unittest.main()
