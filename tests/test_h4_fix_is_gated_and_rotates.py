"""0.0.29 lane H, part H4 — `DOCTOR-FIX-KEEPS-WEAK-TOKEN`, `PRIVATE-READ-ABSENT-VS-REFUSED`,
`PRIVACY-PROBE-LOWS` (a)(d), `CONTAINMENT-IDENTITY-EDGES` (mode/nlink part).

Business rules (note `0029-design/H-security-residue.md` §3a/§3e/§9; rule page
`0029-evidence/H-a/H-a.rule.md` and its attack record):
  * `mokata doctor --fix` shows a PLAN before any change and applies it only on a human's yes at a
    terminal, or `--yes` OUTSIDE an agent's shell (inside one it never applies, `--yes` or not); it
    acts only on the files it showed (one changed since is refused whole); one ledger record says what
    it did (paths, never contents). Plain `doctor` and the MCP `doctor` tool change nothing;
  * a hook token others could read is never kept: rotated, re-baked into every settings file that
    carries it, and the running IDE refuses the old token at once;
  * a settings file others could WRITE holding hooks that are not mokata's is named and never
    rewritten without a second, separate yes (`--yes` never answers it);
  * an exposed IDE is stopped only when its own ping AND the OS's socket table prove which process it
    is; a pid read from a file is never signalled; a weak trust record is reset to not trusted;
  * "no record" (None) and "a record mokata refuses" (`RecordRefused`) are different answers at
    every reader; the shutdown path never raises on a refusal;
  * the volume probe holds under any umask; a record whose mode is not exactly 0600 (dir 0700), or
    which has another hard link, is weak.

"Private" is judged by `_privacy_oracle` (os.stat / `ls -le` / `icacls`), never by `winacl` itself.
Every wait is on an event the test controls (a child's exit or a pipe line), labelled `guard`.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import argparse
import inspect
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

import _privacy_oracle as O

#: guard: a bound on waiting for a CHILD's own event (its exit / its first line). Never what a test
#: asserts on; generous so a slow runner is never a failure.
GUARD = 600


def _silent(*_a, **_k):
    pass


class _Home(unittest.TestCase):
    """A repo and a HOME, both fresh; the user-level IDE state lands in HOME."""

    def setUp(self):
        self._t = tempfile.mkdtemp(prefix="mk-h4-")
        self.addCleanup(shutil.rmtree, self._t, True)
        base = os.path.realpath(self._t)
        self.root = os.path.join(base, "repo")
        self.home = os.path.join(base, "home")
        os.makedirs(os.path.join(self.root, ".mokata", "temp_local"))
        os.makedirs(self.home)
        env = mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CLAUDECODE", None)            # the agent-shell marker (`prompt.agent_shell`)
        from mokata import winacl
        learnt = vars(winacl).get("_HOLDS", {})
        learnt.clear()
        self.addCleanup(learnt.clear)

    # ------------------------------------------------------------------ fixtures
    def _setup(self, copies: int = 0):
        """Real `mokata setup claude --http-gates --ide-bridge`, plus `copies` worktree copies of
        settings.local.json made the way Claude Code makes them (a plain file copy)."""
        from mokata import harness_setup as HS
        plan = HS.plan_setup("claude", root=self.root, home=self.home, http_gates=True,
                             ide_bridge=True)
        HS.apply_setup(plan, assume_yes=True, out=_silent)
        files = [self.local]
        for i in range(copies):
            copy = os.path.join(self.root, ".claude", "worktrees", f"w{i}", ".claude",
                                "settings.local.json")
            os.makedirs(os.path.dirname(copy))
            shutil.copyfile(self.local, copy)
            os.chmod(copy, 0o600)
            files.append(copy)
        return files

    @property
    def local(self):
        return os.path.join(self.root, ".claude", "settings.local.json")

    def _fix(self, tty=True, answer="y", env=None, later="n"):
        """`mokata doctor --fix` with a human at a terminal (`tty`) who answers `answer` to the plan's
        question and `later` to every per-file question after it. There is no `--yes` (round 3)."""
        from mokata.cli_commands.diagnostics import cmd_fix
        out = io.StringIO()
        answers = iter([answer])
        with mock.patch("mokata.prompt._stdin_is_tty", return_value=tty), \
                mock.patch("builtins.input", side_effect=lambda *_a: next(answers, later)), \
                mock.patch.dict(os.environ, env or {}), \
                redirect_stdout(out), redirect_stderr(out):
            rc = cmd_fix(argparse.Namespace(path=self.root))
        return rc, out.getvalue()

    def _snapshot(self):
        """Every file under the repo and HOME: bytes and mtime (what "changed nothing" means)."""
        snap = {}
        for top in (self.root, self.home):
            for dp, _dn, fns in os.walk(top):
                for fn in fns:
                    p = os.path.join(dp, fn)
                    try:
                        st = os.lstat(p)
                        with open(p, "rb") as fh:
                            snap[p] = (fh.read(), st.st_mtime_ns, stat.S_IMODE(st.st_mode))
                    except OSError:
                        snap[p] = None
        return snap

    def _ledger_fix_rows(self):
        from mokata.govern.ledger import AuditLedger
        led = AuditLedger.from_mokata_dir(os.path.join(self.root, ".mokata"))
        return [e for e in led.entries() if e.get("action") == "doctor-fix"]

    def assertPrivate(self, path, what=""):
        self.assertEqual("", O.why_not_private(path), f"{what or path} is not private")


def _baked(path):
    from mokata import harness_setup as HS
    return HS.baked_tokens(Path(path))


# =============================================================== the gate

class FixIsGated(_Home):
    """Nothing changes without a human's yes; the read-only surfaces only stage the plan."""

    def _weak_everything(self):
        from mokata.ide import bus as B
        from mokata.ide import server as S
        from mokata.ide import trust as T
        self._setup(copies=1)
        T.decide(self.root, True, "cli")
        S._write_record(self.root, {"port": 1, "pid": os.getpid(), "token": "t", "root": self.root})
        for p in (B.bridge_path(self.root), self.local, T.record_path(self.root),
                  S.record_path(self.root)):
            O.grant_others_read(p)

    def test_plain_doctor_and_the_plan_write_and_signal_nothing(self):
        from mokata.cli_commands.diagnostics import fix_plan, plan_lines, privacy_lines
        self._weak_everything()
        before = self._snapshot()
        with mock.patch("mokata.quiet_spawn.signal_pid", side_effect=AssertionError("signalled")):
            lines = privacy_lines(self.root) + plan_lines(fix_plan(self.root), staged=True)
        self.assertEqual(before, self._snapshot(), "the plan changed a file")
        text = "\n".join(lines)
        self.assertIn("Staged by `mokata doctor --fix` (nothing changed yet)", text)
        self.assertIn("ROTATE the hook token", text)
        self.assertIn("RESET workspace trust", text)

    def test_the_mcp_doctor_tool_has_no_fix_parameter_and_never_applies(self):
        from mokata.mcp import tools_read
        params = set(inspect.signature(tools_read.doctor).parameters)
        self.assertFalse(params & {"fix", "yes", "apply", "assume_yes"}, params)
        src = Path(tools_read.__file__).read_text(encoding="utf-8")
        for name in ("cmd_fix", "apply_fix", "rebake_token", "write_bridge"):
            self.assertNotIn(name, src, f"the MCP doctor reaches {name}")

    def test_no_terminal_changes_nothing(self):
        # Round 3 (coordinator ruling): THE gate is a human answer typed at a real terminal. Outside
        # any agent's shell, with no marker at all, no terminal still means nothing changes.
        self._weak_everything()
        before = self._snapshot()
        with mock.patch("mokata.prompt.read_yes_no", side_effect=AssertionError("asked")):
            rc, out = self._fix(tty=False)
        self.assertEqual(1, rc)
        self.assertIn("`mokata doctor --fix` will:", out, "the plan is shown even when nobody can answer")
        self.assertIn("nothing was changed — applying this plan needs your answer typed at a terminal",
                      out)
        self.assertEqual(before, self._snapshot())
        self.assertEqual([], self._ledger_fix_rows())

    def test_a_human_no_changes_nothing(self):
        self._weak_everything()
        before = self._snapshot()
        rc, out = self._fix(tty=True, answer="n")
        self.assertEqual(1, rc)
        self.assertEqual(before, self._snapshot())

    def test_an_agent_shell_prints_the_line_and_never_asks(self):
        self._weak_everything()
        before = self._snapshot()
        with mock.patch("mokata.prompt.read_yes_no", side_effect=AssertionError("asked")):
            rc, out = self._fix(tty=True, env={"CLAUDECODE": "1"})
        self.assertEqual(1, rc)
        self.assertIn("this is an agent's shell (CLAUDECODE is set)", out)
        self.assertIn("Run `mokata doctor --fix` in your own terminal", out)
        self.assertEqual(before, self._snapshot())

    def test_an_agent_shell_never_applies_whatever_the_signal(self):
        # Round 2/3 (P2, defense in depth): every signal, any value, refused before anyone is asked.
        self._weak_everything()
        for env in ({"CLAUDECODE": "1"}, {"CLAUDECODE": "true"}):
            with self.subTest(env=env):
                before = self._snapshot()
                with mock.patch("mokata.prompt.read_yes_no", side_effect=AssertionError("asked")):
                    rc, out = self._fix(tty=True, env=env)
                self.assertEqual(1, rc)
                self.assertIn(f"this is an agent's shell ({next(iter(env))} is set)", out)
                self.assertIn("the answer must be yours, typed at your own terminal", out)
                self.assertIn("ROTATE the hook token", out)          # the plan is still shown
                self.assertEqual(before, self._snapshot())

    def test_doctor_has_no_yes(self):
        # Round 3 (coordinator ruling): DELETED, not deprecated — an agent can strip any marker, so no
        # flag may stand in for the human's answer.
        from mokata.cli import build_parser
        with redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as got:
            build_parser().parse_args(["doctor", "--fix", "--yes", "--path", self.root])
        self.assertEqual(2, got.exception.code)
        self.assertIn("--yes", err.getvalue())

    def test_a_yes_handed_in_by_a_caller_means_nothing(self):
        # Nothing in the namespace stands in for the typed answer: no terminal, nothing changes.
        from mokata.cli_commands.diagnostics import cmd_fix
        self._weak_everything()
        before = self._snapshot()
        for extra in ({"yes": True}, {"assume_yes": True}):
            with self.subTest(extra=extra), \
                    mock.patch("mokata.prompt._stdin_is_tty", return_value=False), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(1, cmd_fix(argparse.Namespace(path=self.root, **extra)))
        self.assertEqual(before, self._snapshot())

    def test_one_agent_shell_definition(self):
        # `doctor --fix` and `gate override` read the SAME definition, and neither spells the markers.
        from mokata.cli_commands import diagnostics, gate
        for mod in (diagnostics, gate):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            self.assertIn("agent_shell()", src, mod.__name__)
            self.assertNotIn('environ.get("CLAUDECODE")', src, mod.__name__)
        self.assertFalse(hasattr(diagnostics, "AGENT_SHELL_SIGNALS"), "a second definition")
        # ...and nowhere else in src/ spells a marker as a check (merge with wave 2: approve_codes and
        # `mokata ide --terminal` route through `prompt.agent_shell` too).
        import re
        src = Path(_support.__file__).resolve().parent.parent / "src" / "mokata"
        spelled = re.compile(r"""\.get\(\s*["'](CLAUDECODE)["']""")
        hits = sorted(f.relative_to(src).as_posix() for f in src.rglob("*.py")
                      if f.name != "prompt.py" and spelled.search(f.read_text(encoding="utf-8")))
        self.assertEqual([], hits, "a second agent-shell definition")

    def test_an_empty_signal_is_not_an_agent_shell(self):
        from mokata.prompt import agent_shell
        self.assertEqual("", agent_shell({"CLAUDECODE": ""}))
        self.assertEqual("", agent_shell({}))

    def test_the_app_terminal_panel_is_a_human_shell(self):
        # Jas, 2026-11-16 ("Drop"): the Claude app's Terminal panel sets its own variable for the
        # HUMAN's shell, without CLAUDECODE. It is NOT an agent's shell: `doctor --fix` asks there and
        # applies on the typed yes, and `gate override` asks instead of demanding `--yes`.
        from mokata.ide import bus as B
        from mokata.prompt import agent_shell
        panel = "CLAUDE_CODE_" + "ENTRYPOINT"
        self.assertEqual("", agent_shell({panel: "cli"}))
        self._weak_everything()
        old = json.loads(Path(B.bridge_path(self.root)).read_text(encoding="utf-8"))["token"]
        rc, out = self._fix(tty=True, answer="y", env={panel: "cli"})
        self.assertNotIn("this is an agent's shell", out)
        self.assertNotEqual(old, B.read_bridge(self.root)["token"], out)

    def test_a_human_yes_at_a_terminal_applies(self):
        from mokata.ide import bus as B
        self._weak_everything()
        old = json.loads(Path(B.bridge_path(self.root)).read_text(encoding="utf-8"))["token"]
        rc, out = self._fix(tty=True, answer="y")
        self.assertNotEqual(old, B.read_bridge(self.root)["token"], out)


# =============================================================== rotation

class FixRotatesTheHookToken(_Home):

    def _client(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        return TestClient(build_app(AppState(self.root, "pair", 9999, None, False)),
                          base_url="http://127.0.0.1:9999")

    def _hook(self, client, token):
        return client.post("/api/bus", content=b"{}", headers={"X-Mokata-Hook": token}).status_code

    def test_a_weak_bridge_is_rotated_into_every_copy_and_the_old_token_dies_at_once(self):
        from mokata.ide import bus as B
        files = self._setup(copies=2)
        old = B.read_bridge(self.root)["token"]
        client = self._client()
        self.assertEqual(204, self._hook(client, old), "the control: the old token works")
        O.grant_others_read(B.bridge_path(self.root))
        rc, out = self._fix()
        self.assertEqual(0, rc, out)
        new = B.read_bridge(self.root)["token"]
        self.assertNotEqual(old, new)
        self.assertPrivate(B.bridge_path(self.root))
        for p in files:
            self.assertEqual([new], _baked(p), f"{p} keeps another token")
            self.assertPrivate(p)
            self.assertNotIn(old, Path(p).read_text(encoding="utf-8"))
        # the FIRST request after the rotation carries the old token: refused, no warm-up needed
        self.assertEqual(403, self._hook(client, old), "the running IDE still accepts the old token")
        self.assertEqual(204, self._hook(client, new))
        rows = self._ledger_fix_rows()
        self.assertEqual(1, len(rows), rows)
        self.assertTrue(rows[0]["rotated"] is True, rows[0])
        self.assertEqual(sorted(files + [B.bridge_path(self.root)]), rows[0]["files"])
        self.assertNotIn(new, json.dumps(rows[0]), "the ledger holds a token")
        self.assertIn("must be restarted", out)

    def test_a_weak_settings_file_alone_still_rotates(self):
        from mokata.ide import bus as B
        files = self._setup(copies=1)
        old = B.read_bridge(self.root)["token"]
        O.grant_others_read(files[1])
        rc, out = self._fix()
        self.assertEqual(0, rc, out)
        new = B.read_bridge(self.root)["token"]
        self.assertNotEqual(old, new)
        for p in files:
            self.assertEqual([new], _baked(p))

    def test_a_record_others_could_write_does_not_keep_its_port(self):
        """A port someone else could have written would send the NEW token to their listener."""
        from mokata.ide import bus as B
        self._setup()
        path = B.bridge_path(self.root)
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
        rec["port"] = 1                                   # what an attacker would point it at
        Path(path).write_text(json.dumps(rec), encoding="utf-8")
        O.chmod_like(path, 0o666)
        rc, out = self._fix()
        self.assertEqual(0, rc, out)
        self.assertNotEqual(1, B.read_bridge(self.root)["port"])


class FixIsCrashSafe(_Home):

    def test_a_crash_before_the_record_is_written_is_named_and_the_rerun_finishes(self):
        from mokata.ide import bus as B
        files = self._setup(copies=1)
        O.grant_others_read(B.bridge_path(self.root))
        with mock.patch.object(B, "write_bridge", side_effect=OSError("disk full")):
            rc, out = self._fix()
        self.assertEqual(1, rc)
        self.assertIn("could NOT be written", out)
        self.assertEqual("partial", self._ledger_fix_rows()[-1]["rotated"])
        # the gates block meanwhile: the files carry a token no private record holds
        from mokata.errors import RecordRefused
        with self.assertRaises(RecordRefused):
            B.read_bridge(self.root)
        rc, out = self._fix()
        self.assertEqual(0, rc, out)
        tok = B.read_bridge(self.root)["token"]
        for p in files:
            self.assertEqual([tok], _baked(p))

    def test_a_mismatch_with_a_private_record_is_a_rebake_not_a_rotation(self):
        from mokata import harness_setup as HS
        from mokata.cli_commands.diagnostics import fix_plan
        from mokata.ide import bus as B
        files = self._setup(copies=1)
        rec = B.read_bridge(self.root)
        HS.rebake_token(Path(files[1]), {"port": rec["port"], "token": "stale" * 6}, self.root)
        self.assertEqual(["rebake"], [s.kind for s in fix_plan(self.root)])
        rc, out = self._fix()
        self.assertEqual(0, rc, out)
        self.assertEqual(rec, B.read_bridge(self.root), "a rebake must not rotate")
        for p in files:
            self.assertEqual([rec["token"]], _baked(p))

    def test_one_unparseable_copy_is_named_and_the_rest_are_done(self):
        from mokata.ide import bus as B
        files = self._setup(copies=2)
        broken = files[2]
        Path(broken).write_text('{"X-Mokata-Hook": ', encoding="utf-8")
        os.chmod(broken, 0o600)
        O.grant_others_read(B.bridge_path(self.root))
        rc, out = self._fix()
        self.assertEqual(1, rc)
        self.assertIn(f"{broken}: could not be re-baked", out)
        new = B.read_bridge(self.root)["token"]
        for p in files[:2]:
            self.assertEqual([new], _baked(p))
        self.assertEqual("partial", self._ledger_fix_rows()[-1]["rotated"])


class FixNeverBlessesPlantedHooks(_Home):

    def _plant(self):
        files = self._setup()
        data = json.loads(Path(self.local).read_text(encoding="utf-8"))
        data["hooks"].setdefault("PreToolUse", []).append(
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "/tmp/evil-hook"}]})
        Path(self.local).write_text(json.dumps(data), encoding="utf-8")
        O.chmod_like(self.local, 0o666)                       # others could WRITE it
        return files

    def test_the_plan_names_the_foreign_hook(self):
        from mokata.cli_commands.diagnostics import fix_plan, plan_lines
        self._plant()
        text = "\n".join(plan_lines(fix_plan(self.root)))
        self.assertIn("PreToolUse: command /tmp/evil-hook", text)
        self.assertIn("may hold hooks someone else wrote", text)

    def test_yes_never_answers_the_second_question(self):
        self._plant()
        before = Path(self.local).read_bytes()
        rc, out = self._fix()
        self.assertEqual(1, rc)
        self.assertIn(f"{self.local}: NOT rewritten", out)
        self.assertEqual(before, Path(self.local).read_bytes(), "the planted hooks were blessed")

    def test_a_second_yes_at_a_terminal_keeps_them_and_rewrites(self):
        from mokata.cli_commands.diagnostics import apply_fix, fix_plan
        from mokata.ide import bus as B
        self._plant()
        asked = []
        rc = apply_fix(self.root, fix_plan(self.root), second_yes=lambda p: asked.append(p) or True,
                       out=_silent)
        self.assertEqual(0, rc)
        self.assertEqual([self.local], asked)
        self.assertPrivate(self.local)
        self.assertEqual([B.read_bridge(self.root)["token"]], _baked(self.local))
        self.assertIn("/tmp/evil-hook", Path(self.local).read_text(encoding="utf-8"))

    def test_a_hook_appended_inside_mokatas_group_is_named(self):
        from mokata import harness_setup as HS
        self._setup()
        data = json.loads(Path(self.local).read_text(encoding="utf-8"))
        for groups in data["hooks"].values():
            for g in groups:
                g["hooks"].append({"type": "command", "command": "/tmp/tucked-in"})
        Path(self.local).write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(any("/tmp/tucked-in" in x for x in HS.foreign_hooks(Path(self.local))))


# =============================================================== stopping the IDE

_IDE = r"""
import http.server, json, os, signal, sys
root, pid_mode = sys.argv[1], sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        pid = os.getpid() if pid_mode == "self" else int(pid_mode)
        body = json.dumps({"ok": True, "pid": pid, "root": root, "pages": 2,
                           "terminal": False}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H)
signal.signal(signal.SIGTERM, signal.SIG_IGN if sys.argv[3:] == ["stubborn"] else
              (lambda *a: os._exit(0)))
print(srv.server_address[1], flush=True)
srv.serve_forever()
"""


@unittest.skipIf(os.name == "nt", "the fake IDE uses SIGTERM; Windows cell runs on the mirror probe")
class FixStopsOnlyTheProvenIde(_Home):

    def _spawn(self, argv):
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(self._reap, p)
        return p

    def _reap(self, p):
        if p.poll() is None:
            p.kill()
        p.wait(GUARD)                                    # guard

    def _ide(self, root, pid_mode="self", *more):
        p = self._spawn([sys.executable, "-c", _IDE, root, pid_mode, *more])
        port = int(p.stdout.readline())                  # the child's own event: it is listening
        return p, port

    def _weak_record(self, port, pid):
        from mokata.ide import server as S
        S._write_record(self.root, {"port": port, "pid": pid, "token": "tok", "root": self.root})
        O.grant_others_read(S.record_path(self.root))
        return S.record_path(self.root)

    def _signals(self):
        from mokata import quiet_spawn
        real, sent = quiet_spawn.signal_pid, []

        def spy(pid, sig):
            sent.append(pid)
            return real(pid, sig)
        return mock.patch.object(quiet_spawn, "signal_pid", side_effect=spy), sent

    def test_the_real_ide_is_stopped_by_its_proven_pid_never_the_files(self):
        ide, port = self._ide(self.root)
        victim = self._spawn([sys.executable, "-c", "import time; time.sleep(3600)"])
        rec = self._weak_record(port, victim.pid)        # the FILE names another live process
        patch, sent = self._signals()
        with patch:
            rc, out = self._fix()
        self.assertEqual(0, rc, out)
        self.assertEqual([ide.pid], sent)
        self.assertEqual(0, ide.wait(GUARD))             # guard: the fake IDE exits 0 on SIGTERM
        self.assertIsNone(victim.poll(), "the pid in the file was signalled")
        self.assertFalse(os.path.exists(rec))
        self.assertIn(f"waiting for the IDE (pid {ide.pid}) to stop", out)
        self.assertEqual(ide.pid, self._ledger_fix_rows()[-1]["stopped"])
        # Low 5: the plan promises the stop only on the OS's proof, never on the ping alone.
        self.assertIn("— only once the OS shows that pid is the process listening on its port", out)

    def test_an_ide_that_keeps_answering_is_said_and_its_record_kept(self):
        """The wait is on the IDE's OWN ping: a record that cannot be read (it is weak) must not make
        a still-running IDE look stopped."""
        ide, port = self._ide(self.root, "self", "stubborn")
        rec = self._weak_record(port, ide.pid)
        patch, sent = self._signals()
        with patch:
            rc, out = self._fix()
        self.assertEqual(1, rc)
        self.assertEqual([ide.pid], sent)
        self.assertIsNone(ide.poll())
        self.assertIn(f"the IDE (pid {ide.pid}) did not stop; nothing else was changed for it", out)
        self.assertTrue(os.path.exists(rec), "the record of a running IDE was removed")

    def test_a_listener_that_lies_about_its_pid_gets_nothing_signalled(self):
        victim = self._spawn([sys.executable, "-c", "import time; time.sleep(3600)"])
        liar, port = self._ide(self.root, str(victim.pid))
        self._weak_record(port, victim.pid)
        patch, sent = self._signals()
        with patch:
            rc, out = self._fix()
        self.assertEqual(1, rc)
        self.assertEqual([], sent)
        self.assertIsNone(victim.poll())
        self.assertIsNone(liar.poll())
        self.assertIn("could not be proven", out)

    def test_a_record_naming_a_dead_pid_and_no_listener(self):
        dead = self._spawn([sys.executable, "-c", "pass"])
        dead.wait(GUARD)                                  # guard
        ide, port = self._ide(self.root)
        ide.kill()
        ide.wait(GUARD)                                   # guard: nothing listens on `port` now
        rec = self._weak_record(port, dead.pid)
        patch, sent = self._signals()
        with patch:
            rc, out = self._fix()
        self.assertEqual([], sent)
        self.assertIn("no IDE answered on the record's port: nothing was stopped", out)
        self.assertFalse(os.path.exists(rec), "the weak record must be removed, and said")

    def test_an_ide_of_another_repository_is_not_signalled(self):
        other = os.path.join(os.path.realpath(self._t), "other")
        os.makedirs(other)
        ide, port = self._ide(other)
        self._weak_record(port, ide.pid)
        patch, sent = self._signals()
        with patch:
            rc, out = self._fix()
        self.assertEqual([], sent)
        self.assertIsNone(ide.poll())
        self.assertIn("serves another repository: nothing was stopped", out)

    def test_an_ide_replaced_between_plan_and_apply_is_not_signalled(self):
        from mokata.cli_commands.diagnostics import apply_fix, fix_plan
        ide, port = self._ide(self.root)
        self._weak_record(port, ide.pid)
        steps = fix_plan(self.root)
        for s in steps:
            if s.kind == "stop-ide":
                s.extra["pid"] = ide.pid + 1               # what the human was shown
        patch, sent = self._signals()
        with patch:
            rc = apply_fix(self.root, steps, out=_silent)
        self.assertEqual(1, rc)
        self.assertEqual([], sent)
        self.assertIsNone(ide.poll())


# =============================================================== trust

class FixNeverAdoptsTrust(_Home):

    def _weak_trusted(self, mode):
        from mokata.ide import trust as T
        T.decide(self.root, True, "cli")
        O.chmod_like(T.record_path(self.root), mode)
        return T.record_path(self.root)

    def test_a_readable_trusted_record_is_reset(self):
        from mokata.ide import trust as T
        path = self._weak_trusted(0o644)
        rc, out = self._fix()
        self.assertEqual(0, rc, out)
        self.assertFalse(T.status(self.root)["trusted"])
        self.assertPrivate(path)
        how = "writable" if os.name == "nt" else "readable"     # nt: write assumed (see above)
        self.assertIn(f"because the record was {how} by others: re-trust in the IDE", out)
        self.assertTrue(self._ledger_fix_rows()[-1]["trust_reset"])

    @unittest.skipIf(os.name == "nt", "POSIX write bit")
    def test_the_words_follow_the_verdict_for_a_writable_one(self):
        from mokata.ide import trust as T
        self._weak_trusted(0o666)
        rc, out = self._fix()
        self.assertIn("because the record was writable by others", out)
        self.assertFalse(T.status(self.root)["trusted"])


# =============================================================== foreign: untouched

@unittest.skipIf(os.name == "nt", "the uid seam is POSIX")
class ForeignIsUntouched(_Home):

    def test_a_foreign_bridge_record_is_named_and_nothing_is_rotated_from_it(self):
        from mokata.cli_commands.diagnostics import fix_plan
        from mokata.ide import bus as B
        self._setup()
        path = B.bridge_path(self.root)
        before, mtime = Path(path).read_bytes(), os.stat(path).st_mtime_ns
        local = Path(self.local).read_bytes()
        with mock.patch("os.geteuid", return_value=os.geteuid() + 4242):
            kinds = [s.kind for s in fix_plan(self.root)]
            rc, out = self._fix()
        self.assertNotIn("rotate", kinds)
        self.assertIn("refuse", kinds)
        self.assertEqual(before, Path(path).read_bytes())
        self.assertEqual(mtime, os.stat(path).st_mtime_ns)
        self.assertEqual(local, Path(self.local).read_bytes())


# =============================================================== absent is not refused

class AbsentIsNotRefused(_Home):

    def _weak(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Path(path).write_text(json.dumps(data), encoding="utf-8")
        O.grant_others_read(path)

    def test_read_private_none_only_when_absent(self):
        from mokata import winacl
        from mokata.errors import RecordRefused
        p = os.path.join(self.root, "r.json")
        self.assertIsNone(winacl.read_private(p))
        self._weak(p, {})
        with self.assertRaises(RecordRefused) as cm:
            winacl.read_private(p)
        self.assertEqual(p, cm.exception.path)
        self.assertIn("`mokata doctor --fix`", cm.exception.line())

    def test_each_reader_says_refused(self):
        from mokata.errors import RecordRefused
        from mokata.ide import bridge_client as BC
        from mokata.ide import bus as B
        from mokata.ide import launch as L
        from mokata.ide import server as S
        from mokata.ide import trust as T
        self._weak(B.bridge_path(self.root), {"port": 24444, "token": "x"})
        self._weak(S.record_path(self.root), {"port": 4444, "pid": 1, "token": "x",
                                              "root": self.root})
        self._weak(T.record_path(self.root), {"trusted": True, "root": T._real(self.root)})
        with self.assertRaises(RecordRefused):
            B.read_bridge(self.root)
        with self.assertRaises(RecordRefused):
            S.read_record(self.root)
        st = T.status(self.root)
        self.assertFalse(st["trusted"])
        self.assertIn("refused", st["refused"])
        self.assertIsNone(L.running(self.root))
        self.assertIsNone(BC.live_ide(self.root))
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(0, B.preferred_port(self.root))
        self.assertIn(f"{B.bridge_path(self.root)} refused", err.getvalue())

    def test_shutdown_on_a_refused_record_says_so_and_never_raises(self):
        from mokata.ide import server as S
        self._weak(S.record_path(self.root), {"port": 4444, "pid": os.getpid(), "token": "x",
                                              "root": self.root})
        err = io.StringIO()
        with redirect_stderr(err):
            S._remove_record(self.root, os.getpid())
        self.assertIn("left in place on shutdown", err.getvalue())
        self.assertTrue(os.path.exists(S.record_path(self.root)))

    def test_server_start_refuses_and_names_it(self):
        from mokata.ide import server as S
        self._weak(S.record_path(self.root), {"port": 4444, "pid": 1, "token": "x",
                                              "root": self.root})
        err = io.StringIO()

        class Started(Exception):
            pass
        # a server that would START over the refused record raises here instead of serving forever
        with redirect_stderr(err), mock.patch("uvicorn.Config", side_effect=Started), \
                mock.patch.object(S, "_write_record", side_effect=Started):
            try:
                rc = S.serve(self.root, 0)
            except Started:
                self.fail("the server started over a refused record")
        self.assertEqual(1, rc)
        self.assertIn("refused", err.getvalue())

    def test_mcp_ide_status_names_the_refused_record(self):
        from mokata.ide import bus as B
        from mokata.mcp import tools_ide
        self._weak(B.bridge_path(self.root), {"port": 24444, "token": "x"})
        with mock.patch.object(tools_ide, "_root", return_value=self.root):
            got = tools_ide.ide_status(self.root)
        self.assertFalse(got["running"])
        self.assertTrue(any(B.bridge_path(self.root) in x for x in got.get("refused", [])), got)

    def test_the_cli_names_a_refused_server_record(self):
        from mokata.cli_commands import ide as CI
        from mokata.ide import server as S
        self._weak(S.record_path(self.root), {"port": 4444, "pid": 1, "token": "x",
                                              "root": self.root})
        args = argparse.Namespace(path=self.root, trust=False, untrust=False, target=None,
                                  foreground=False, demo=False, session=None, no_browser=True,
                                  terminal=False, port=0)
        err = io.StringIO()
        from mokata.ide import launch as L
        # a launcher that would START a server over the refused record fails here, never spawns one
        with redirect_stderr(err), redirect_stdout(io.StringIO()), \
                mock.patch.object(L, "_open_or_focus", side_effect=AssertionError("started")):
            rc = CI.cmd_ide(args)
        self.assertEqual(1, rc)
        self.assertIn("refused", err.getvalue())
        self.assertIn("mokata doctor --fix", err.getvalue())


# =============================================================== probe and verdicts

@unittest.skipIf(os.name == "nt", "POSIX modes")
class ProbeIgnoresUmask(_Home):

    def test_the_probe_holds_under_a_narrow_umask(self):
        from mokata import winacl
        d = os.path.join(self.root, "p")
        os.makedirs(d)
        old = os.umask(0o277)
        try:
            winacl._HOLDS.clear()
            self.assertTrue(winacl._probe(d), "a umask made a mode-holding volume look modeless")
        finally:
            os.umask(old)

    def test_a_private_record_is_born_exactly_0600_under_a_narrow_umask(self):
        from mokata.ide import bus as B
        os.makedirs(os.path.dirname(B.bridge_path(self.root)))   # a plain mkdir under 0o277 is 0o500
        old = os.umask(0o277)
        try:
            rec = B.ensure_bridge(self.root)
        finally:
            os.umask(old)
        self.assertEqual(0o600, stat.S_IMODE(os.stat(B.bridge_path(self.root)).st_mode))
        self.assertEqual(rec, B.read_bridge(self.root))


@unittest.skipIf(os.name == "nt", "POSIX modes")
class WeakModes(_Home):

    def _verdict(self, path):
        from mokata import winacl
        return winacl.verify_private(path)

    def _file(self, mode):
        p = os.path.join(self.root, "r.json")
        Path(p).write_text("{}", encoding="utf-8")
        os.chmod(p, mode)
        return p

    def test_0400_is_weak_and_called_harmless(self):
        v = self._verdict(self._file(0o400))
        self.assertFalse(v.private)
        self.assertIn("harmless", v.reason)

    def test_setuid_0600_is_weak(self):
        p = self._file(0o600)
        try:
            os.chmod(p, 0o4600)
        except OSError:
            self.skipTest("NOT RUN: this volume refuses a setuid bit")
        if stat.S_IMODE(os.stat(p).st_mode) != 0o4600:
            self.skipTest("NOT RUN: the setuid bit did not stick")
        self.assertFalse(self._verdict(p).private)

    def test_a_second_hard_link_is_weak(self):
        p = self._file(0o600)
        os.link(p, p + ".2")
        v = self._verdict(p)
        self.assertFalse(v.private)
        self.assertIn("hard links", v.reason)

    def test_exact_0600_is_private(self):
        self.assertTrue(self._verdict(self._file(0o600)).private)


@unittest.skipUnless(os.name == "nt", "NOT RUN off Windows: the volume query is an nt call")
class VolumeQueryFails(_Home):

    def test_a_failed_volume_query_checks_the_acl(self):
        from mokata import winacl
        p = os.path.join(self.root, "r.json")
        Path(p).write_text("{}", encoding="utf-8")
        O.grant_others_read(p)
        _ctypes, _wt, _adv, ker = winacl._win()
        with mock.patch.object(ker, "GetVolumeInformationByHandleW", return_value=0):
            v = winacl.verify_private(p)
        self.assertTrue(v.holds, v)
        self.assertFalse(v.private, v)



class FixActsOnlyOnWhatItShowed(_Home):
    """Round 2, M3: the yes is to the plan that was SHOWN. A settings file that differs at apply time
    (a hook planted after the plan) is never acted on: nothing at all changes, and it says so."""

    def _weak_bridge_plan(self):
        from mokata.cli_commands.diagnostics import fix_plan
        from mokata.ide import bus
        self._setup()
        O.grant_others_read(bus.bridge_path(self.root))
        return fix_plan(self.root)

    def test_a_private_file_changed_after_the_plan_refuses_the_whole_apply(self):
        from mokata.cli_commands.diagnostics import apply_fix
        steps = self._weak_bridge_plan()
        data = json.loads(Path(self.local).read_text(encoding="utf-8"))
        data["hooks"].setdefault("PreToolUse", []).append(
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "/late/hook"}]})
        with open(self.local, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        before = self._snapshot()
        out = []
        rc = apply_fix(self.root, steps, second_yes=lambda p: True, out=out.append)
        self.assertEqual(1, rc)
        self.assertEqual(before, self._snapshot(), "something changed on a plan nobody saw")
        self.assertIn(f"{self.local}: changed since the plan was shown", out)
        self.assertIn("nothing was changed — re-run `mokata doctor --fix` and read the new plan", out)

    def test_an_unchanged_file_still_applies(self):
        from mokata.cli_commands.diagnostics import apply_fix
        steps = self._weak_bridge_plan()
        rc = apply_fix(self.root, steps, second_yes=None, out=_silent)
        self.assertEqual(0, rc)

    def test_a_file_that_moves_while_the_plan_reads_it_is_never_applied(self):
        from mokata import harness_setup as HS
        from mokata.cli_commands import diagnostics as D
        from mokata.ide import bus
        self._setup()
        O.grant_others_read(bus.bridge_path(self.root))
        real = HS.foreign_hooks

        def planted_mid_read(path):
            got = real(path)                       # what the plan SHOWS: no foreign hook
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            data["hooks"].setdefault("Stop", []).append(
                {"hooks": [{"type": "command", "command": "/mid/read"}]})
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            return got

        with mock.patch.object(HS, "foreign_hooks", planted_mid_read):
            steps = D.fix_plan(self.root)
        before = self._snapshot()
        rc = D.apply_fix(self.root, steps, second_yes=lambda p: True, out=_silent)
        self.assertEqual(1, rc)
        self.assertEqual(before, self._snapshot())

    def test_a_file_changed_after_the_apply_check_is_still_not_rewritten(self):
        # The race past the apply's own check: each re-bake carries the plan's digest down to the one
        # read it writes from.
        from mokata import harness_setup as HS
        from mokata.cli_commands.diagnostics import apply_fix
        steps = self._weak_bridge_plan()
        shown = {p: HS.file_digest(p) for s in steps for p in s.digests}
        with open(self.local, "a", encoding="utf-8") as fh:
            fh.write(" ")
        late = Path(self.local).read_bytes()
        out = []
        with mock.patch.object(HS, "file_digest", lambda p: shown.get(p)):
            rc = apply_fix(self.root, steps, second_yes=lambda p: True, out=out.append)
        self.assertEqual(1, rc)
        self.assertEqual(late, Path(self.local).read_bytes())
        self.assertTrue(any("changed since the plan was shown: not rewritten" in o for o in out), out)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO")
    def test_a_fifo_swapped_in_after_the_plan_never_hangs_and_changes_nothing(self):
        import threading
        from mokata import harness_setup as HS
        from mokata.cli_commands.diagnostics import apply_fix
        steps = self._weak_bridge_plan()
        os.unlink(self.local)
        os.mkfifo(self.local)
        before, res = self._snapshot_without(self.local), {}

        def run():
            res["digest"] = HS.file_digest(self.local)
            res["rc"] = apply_fix(self.root, steps, second_yes=lambda p: True, out=_silent)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(60)          # guard: both return in milliseconds; a blocked open never returns at all
        while t.is_alive():
            try:                                        # unblock every blocked open, then fail
                os.close(os.open(self.local, os.O_WRONLY | os.O_NONBLOCK))
            except OSError:
                pass
            t.join(1)
            res["hung"] = True
        self.assertNotIn("hung", res, "a FIFO at a settings path blocked the digest or the apply")
        self.assertIsNone(res["digest"], "a FIFO has no digest")
        self.assertEqual(1, res["rc"])
        self.assertEqual(before, self._snapshot_without(self.local))
        self.assertTrue(stat.S_ISFIFO(os.lstat(self.local).st_mode), "the FIFO was replaced")

    def _snapshot_without(self, skip):
        snap = {}
        for top in (self.root, self.home):
            for dp, _dn, fns in os.walk(top):
                for fn in fns:
                    p = os.path.join(dp, fn)
                    if p != skip:
                        with open(p, "rb") as fh:
                            snap[p] = fh.read()
        return snap

    def test_the_rebake_reads_once_and_writes_only_the_bytes_it_was_shown(self):
        # The narrow window after the apply's check: `rebake_token(expect=)` re-checks the bytes it
        # read ONCE and writes nothing when they are not the plan's.
        from mokata import harness_setup as HS
        from mokata.ide import bus
        self._setup()
        rec = bus.read_bridge(self.root)
        shown = HS.file_digest(self.local)
        with open(self.local, "a", encoding="utf-8") as fh:
            fh.write(" ")
        before = Path(self.local).read_bytes()
        with self.assertRaises(HS.ChangedSincePlan):
            HS.rebake_token(Path(self.local), dict(rec, token="new"), self.root, expect=shown)
        self.assertEqual(before, Path(self.local).read_bytes())
        self.assertTrue(HS.rebake_token(Path(self.local), dict(rec, token="new"), self.root,
                                        expect=HS.file_digest(self.local)))
        self.assertEqual(["new"], HS.baked_tokens(Path(self.local)))


class APlantedPortIsNeverKept(_Home):
    """A weak bridge record someone else could have WRITTEN (a write bit, or another hard link) never
    keeps its port: the new token would be baked into URLs for a listener of theirs."""

    def _planned_port(self, mutate):
        from mokata.cli_commands.diagnostics import fix_plan
        from mokata.ide import bus
        self._setup()
        p = bus.bridge_path(self.root)
        rec = json.loads(Path(p).read_text(encoding="utf-8"))
        rec["port"] = 31337
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        mutate(p)
        return [s for s in fix_plan(self.root) if s.kind == "rotate"][0].extra["port"]

    def test_a_hard_linked_record_drops_its_port(self):
        self.assertEqual(0, self._planned_port(lambda p: (O.chmod_like(p, 0o644), os.link(p, p + ".x"))))

    def test_a_group_writable_record_drops_its_port(self):
        self.assertEqual(0, self._planned_port(lambda p: O.chmod_like(p, 0o664)))

    def test_a_record_only_others_could_read_keeps_its_port(self):
        # Windows: `_others_may_write` cannot tell read from write in a DACL and assumes write, so the
        # port is dropped there too — the conservative side (diagnostics._others_may_write).
        self.assertEqual(31337 if os.name != "nt" else 0,
                         self._planned_port(lambda p: O.chmod_like(p, 0o644)))


class PlanTextIsNeverInterpreted(_Home):
    """A path or a hook command is printed as text: a control character is shown as `?`, never sent
    to the terminal (the plan is read by a human before a yes)."""

    BAD = "\x1b]0;owned\x07\x1b[2J"

    def test_plan_lines_replace_control_characters(self):
        from mokata.cli_commands.diagnostics import FixStep, plan_lines
        step = FixStep("refuse", "/r/a" + self.BAD, "why" + self.BAD)
        text = "\n".join(plan_lines([step]))
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertIn("/r/a?]0;owned??[2J", text)

    def test_foreign_hook_lines_replace_control_characters(self):
        from mokata import harness_setup as HS
        self._setup()
        data = json.loads(Path(self.local).read_text(encoding="utf-8"))
        data["hooks"].setdefault("Stop", []).append(
            {"hooks": [{"type": "command", "command": "echo" + self.BAD}]})
        with open(self.local, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        lines = HS.foreign_hooks(Path(self.local))
        self.assertEqual(["Stop: command echo?]0;owned??[2J"], lines)


class OnlyTheListenerIsProven(unittest.TestCase):
    """A pid with an ESTABLISHED connection to the port is not the process LISTENING on it: the OS
    proof names only the listener (a ping can be answered by anyone's listener)."""

    @unittest.skipIf(os.name == "nt", "the nt socket table is graded in the Windows cell")
    def test_a_connected_client_is_not_the_listener(self):
        import socket
        from mokata.ide import launch
        srv = socket.socket()
        self.addCleanup(srv.close)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        child = subprocess.Popen(
            [sys.executable, "-c", "import socket,sys; s=socket.create_connection(('127.0.0.1', %d));"
             " print('up', flush=True); sys.stdin.read()" % port],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(child.wait, GUARD)
        self.addCleanup(child.stdin.close)
        self.addCleanup(child.stdout.close)
        self.assertEqual("up", child.stdout.readline().strip())    # guard: the child's own line
        conn, _ = srv.accept()
        self.addCleanup(conn.close)
        if launch.listener_owned(os.getpid(), port) is None:
            self.skipTest("this OS gives no socket table here")
        self.assertTrue(launch.listener_owned(os.getpid(), port))
        self.assertFalse(launch.listener_owned(child.pid, port))


class HookTokenSignatureCountsSize(_Home):
    """`hook_token_now` re-reads the record when ANY of dev/ino/size/mtime/ctime moved: a same-mtime,
    same-ctime rewrite of another size (Windows: ctime is the creation time) is still re-read."""

    def test_a_size_change_alone_re_reads_the_record(self):
        from mokata.ide import bus
        from mokata.ide import server as S
        self._setup()
        state = S.AppState(self.root, "pair", 9999, None, False)
        first = state.hook_token_now()
        self.assertTrue(first)
        path = bus.bridge_path(self.root)
        real = os.stat
        size = {"n": 100}

        def fixed_times(p, *a, **k):
            st = real(p, *a, **k)
            if str(p) != path:
                return st
            return mock.Mock(st_dev=st.st_dev, st_ino=st.st_ino, st_size=size["n"],
                             st_mtime_ns=1, st_ctime_ns=1)

        with mock.patch.object(S.os, "stat", fixed_times):
            state.hook_token_now()                 # signature with the fixed times
            ino, rotated = os.lstat(path).st_ino, dict(bus.read_bridge(self.root), token="rotated")
            with open(path, "w", encoding="utf-8") as fh:      # in place: same inode, same mode
                json.dump(rotated, fh)
            self.assertEqual(ino, os.lstat(path).st_ino)
            size["n"] = 101
            self.assertEqual("rotated", state.hook_token_now())


class HookTokenOnWindowsReReadsEveryCall(_Home):
    """0.0.29 H-a xplat: on Windows `st_ctime` is the CREATION time, so a same-size in-place rewrite
    with the mtime put back (or an ACL change, or a new hard link) leaves the stat signature still.
    Where the signature cannot see every change (`HOOK_SIG_SEES_EVERY_CHANGE` False) the record is
    re-read on EVERY call. Graded on POSIX by freezing the whole signature and clearing the flag."""

    def test_an_unchanged_signature_still_re_reads_where_it_cannot_see_every_change(self):
        from mokata.ide import bus
        from mokata.ide import server as S
        self._setup()
        state = S.AppState(self.root, "pair", 9999, None, False)
        self.assertTrue(state.hook_token_now())
        path = bus.bridge_path(self.root)
        rotated = dict(bus.read_bridge(self.root), token="rotated")
        real = os.stat

        def frozen(p, *a, **k):
            st = real(p, *a, **k)
            if str(p) != path:
                return st
            return mock.Mock(st_dev=1, st_ino=1, st_size=1, st_mtime_ns=1, st_ctime_ns=1)

        with mock.patch.object(S.os, "stat", frozen), \
                mock.patch.object(S, "HOOK_SIG_SEES_EVERY_CHANGE", False):
            state.hook_token_now()                 # the frozen signature is now the known one
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rotated, fh)
            self.assertEqual("rotated", state.hook_token_now())

    def test_the_flag_is_off_exactly_on_windows(self):
        from mokata.ide import server as S
        self.assertEqual(os.name != "nt", S.HOOK_SIG_SEES_EVERY_CHANGE)


class ANulStdinIsNotATerminal(unittest.TestCase):
    """0.0.29 H-a xplat: Windows `isatty()` is True for the NUL device, so `doctor --fix < NUL`
    was ASKED (and read EOF). On Windows a stream is a terminal only when the console answers
    `GetConsoleMode` for its handle. Graded on POSIX by forcing the console check on."""

    class _Lying:
        def __init__(self, fd):
            self._fd = fd

        def isatty(self):
            return True

        def fileno(self):
            return self._fd

    def test_a_stream_that_claims_a_tty_but_the_console_denies_is_refused(self):
        from mokata import prompt
        with mock.patch.object(prompt, "CONSOLE_CHECK", True), \
                mock.patch.object(prompt, "_console_answers", return_value=False) as asked:
            self.assertFalse(prompt.real_terminal(self._Lying(0)))
            with mock.patch.object(prompt.sys, "stdin", self._Lying(0)):
                self.assertFalse(prompt._stdin_is_tty())
        asked.assert_called_with(0)

    def test_the_console_confirming_it_is_a_terminal(self):
        from mokata import prompt
        with mock.patch.object(prompt, "CONSOLE_CHECK", True), \
                mock.patch.object(prompt, "_console_answers", return_value=True):
            self.assertTrue(prompt.real_terminal(self._Lying(0)))

    def test_every_terminal_door_uses_the_one_check(self):
        from mokata import prompt
        from mokata.cli_commands import ide as cli_ide
        from mokata.ide import approve_codes
        with mock.patch.object(prompt, "CONSOLE_CHECK", True), \
                mock.patch.object(prompt, "_console_answers", return_value=False), \
                mock.patch.object(prompt.sys, "stdin", self._Lying(0)), \
                mock.patch.object(prompt.sys, "stdout", self._Lying(1)):
            self.assertFalse(cli_ide._human_tty())
            ok, _why = approve_codes.human_terminal({}, self._Lying(0), self._Lying(1))
            self.assertFalse(ok)

    def test_on_this_os_the_check_is_on_exactly_on_windows(self):
        import sys as _sys
        from mokata import prompt
        self.assertEqual(_sys.platform == "win32", prompt.CONSOLE_CHECK)

    @unittest.skipUnless(sys.platform == "win32", "the NUL device and GetConsoleMode are Windows-only")
    def test_the_real_nul_device_is_not_a_terminal_on_windows(self):
        from mokata import prompt
        with open("NUL", "r", encoding="utf-8") as nul:
            self.assertTrue(nul.isatty())          # the lie this check exists for
            self.assertFalse(prompt.real_terminal(nul))


class NoAclVolumeAsksTheSecondYes(_Home):
    """Low 6: a settings file on a volume that holds no access control could have been written by
    anyone; holding foreign hooks, it is rewritten only on the second, separate yes."""

    def test_noacl_with_foreign_hooks_needs_the_second_yes(self):
        from mokata import winacl
        from mokata.cli_commands import diagnostics as D
        from mokata.ide import bus
        self._setup()
        O.grant_others_read(bus.bridge_path(self.root))
        data = json.loads(Path(self.local).read_text(encoding="utf-8"))
        data["hooks"].setdefault("Stop", []).append(
            {"hooks": [{"type": "command", "command": "/theirs"}]})
        with open(self.local, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        real = D._examine

        def noacl(path):
            state, v, raw = real(path)
            if path == self.local:
                return "noacl", winacl.Verdict(False, True, False, "no access control"), raw
            return state, v, raw

        with mock.patch.object(D, "_examine", noacl):
            steps = D.fix_plan(self.root)
        rotate = [s for s in steps if s.kind == "rotate"][0]
        self.assertIn(self.local, rotate.second_yes)


class FirstRunMoveLeavesARefusedRecord(_Home):
    """Low 7: the first-run move of the IDE's own server record never moves (or raises on) a record
    mokata refuses; the refused file stays where it is."""

    def test_a_refused_old_record_is_not_moved(self):
        from mokata.ide import firstrun, server as S
        from mokata.ide.statedir import user_repo_dir
        old = os.path.join(user_repo_dir(self.root), "ide", "server.json")
        os.makedirs(os.path.dirname(old), exist_ok=True)
        with open(old, "w", encoding="utf-8") as fh:
            json.dump({"port": 1, "pid": os.getpid(), "token": "t", "root": self.root}, fh)
        O.chmod_like(old, 0o644)
        before = Path(old).read_bytes()
        firstrun._move_record(self.root)
        self.assertEqual(before, Path(old).read_bytes())
        self.assertNotEqual(old, S.record_path(self.root))
        self.assertFalse(os.path.exists(S.record_path(self.root)), "a refused record was moved")


class RefusedRecordNeverTracebacks(unittest.TestCase):
    """Round 2, M4: every caller of `open_or_focus`, `read_record` (the IDE server's) and
    `read_bridge` in src/ meets a `RecordRefused` inside a handler that catches it (or a broader
    class), except the declared sites below — exact both ways, so a new narrow catch reds here.
    Round 3: the names are resolved through every import spelling — relative, absolute, aliased,
    `import a.b.c as m` and a dotted call (`mokata.ide.server.read_record`)."""

    #: (file, function): propagates by contract, or a file another lane owns (named in the report).
    DECLARED = {
        ("ide/launch.py", "open_or_focus"): "raises RecordRefused by contract; its callers catch it",
    }
    CATCHES = {"RecordRefused", "MokataError", "Exception", "BaseException"}
    #: function -> the module (last dotted part) that defines it
    TARGETS = {"read_record": "server", "read_bridge": "bus", "open_or_focus": "launch"}

    @classmethod
    def sites_in(cls, rel, source):
        import ast
        tree = ast.parse(source)
        own = rel.rsplit("/", 1)[-1][:-3]
        names = {f for f, mod in cls.TARGETS.items() if own == mod and rel.startswith("ide/")}
        mods = {}                                   # local name -> module it stands for (last part)

        def ide_module(dotted):
            parts = (dotted or "").split(".")
            return parts[-1] if parts[-1] in cls.TARGETS.values() and (
                len(parts) == 1 or parts[-2] == "ide") else ""

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = ide_module(node.module)
                for a in node.names:
                    if mod and cls.TARGETS.get(a.name) == mod:
                        names.add(a.asname or a.name)
                    elif (node.module or "").split(".")[-1] in ("ide", "") and \
                            a.name in cls.TARGETS.values():
                        mods[a.asname or a.name] = a.name
            elif isinstance(node, ast.Import):
                for a in node.names:
                    mod = ide_module(a.name)
                    if mod and a.asname:
                        mods[a.asname] = mod

        def dotted(expr):
            if isinstance(expr, ast.Name):
                return expr.id
            if isinstance(expr, ast.Attribute):
                head = dotted(expr.value)
                return f"{head}.{expr.attr}" if head else ""
            return ""

        def target(call):
            fn = call.func
            if isinstance(fn, ast.Name):
                return fn.id in names
            if isinstance(fn, ast.Attribute) and fn.attr in cls.TARGETS:
                if fn.attr in ("open_or_focus", "read_bridge"):
                    return True                      # conservative: any receiver
                base = dotted(fn.value)
                return mods.get(base) == "server" or ide_module(base) == "server"
            return False

        def caught(handlers):
            for h in handlers:
                if h.type is None:
                    return True
                for t in (h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]):
                    name = t.id if isinstance(t, ast.Name) else getattr(t, "attr", "")
                    if name in cls.CATCHES:
                        return True
            return False

        found = set()

        def walk(node, func, guarded):
            for child in ast.iter_child_nodes(node):
                is_def = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                f2 = child.name if is_def else func
                g2 = False if is_def else guarded
                if isinstance(child, ast.Try):
                    inner = g2 or caught(child.handlers)
                    for b in child.body:
                        visit(b, f2, inner)
                    for b in child.handlers + child.orelse + child.finalbody:
                        visit(b, f2, g2)
                    continue
                visit(child, f2, g2, descend=False)
                walk(child, f2, g2)

        def visit(node, func, guarded, descend=True):
            if isinstance(node, ast.Call) and target(node) and not guarded:
                found.add((rel, func))
            if descend:
                walk(node, func, guarded)

        walk(tree, "<module>", False)
        return found

    def _sites(self):
        src = Path(_support.__file__).resolve().parent.parent / "src" / "mokata"
        found = set()
        for f in sorted(src.rglob("*.py")):
            found |= self.sites_in(f.relative_to(src).as_posix(), f.read_text(encoding="utf-8"))
        return found

    def test_every_reader_catches_a_refusal(self):
        self.assertEqual(set(self.DECLARED), self._sites())

    def test_every_import_spelling_is_seen(self):
        # The round-2 verifier's probe shapes (verify-r2 Low 2): each one is an unguarded reader.
        probes = {
            "aliased": "from .ide.bus import read_bridge as rb\ndef f(r):\n    return rb(r)\n",
            "absolute": "from mokata.ide import server\ndef f(r):\n    return server.read_record(r)\n",
            "import-as": "import mokata.ide.server as S\ndef f(r):\n    return S.read_record(r)\n",
            "dotted": "import mokata.ide.server\ndef f(r):\n    return mokata.ide.server.read_record(r)\n",
            "relative-module": "from ..ide import launch as L\ndef f(r):\n    return L.open_or_focus(r, None)\n",
            "absolute-name": "from mokata.ide.server import read_record as rr\ndef f(r):\n    return rr(r)\n",
        }
        for label, code in probes.items():
            with self.subTest(label):
                self.assertEqual({("x/probe.py", "f")}, self.sites_in("x/probe.py", code))
        guarded = ("from mokata.ide.bus import read_bridge as rb\nfrom mokata.errors import RecordRefused\n"
                   "def f(r):\n    try:\n        return rb(r)\n    except RecordRefused:\n        return None\n")
        self.assertEqual(set(), self.sites_in("x/probe.py", guarded))
        other = "from .model_tiers import read_record\ndef f(r):\n    return read_record(r)\n"
        self.assertEqual(set(), self.sites_in("x/probe.py", other), "another module's read_record")


if __name__ == "__main__":
    unittest.main()
