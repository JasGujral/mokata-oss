"""0.0.29 H-a VERIFIER attack tests (refuting verifier, not the builder).

Each test asserts the CORRECT behaviour per the H note / amendments. A test that fails is a finding;
the verify report says which. Product code is exercised for real (real settings files from
`mokata setup`, the real IDE app through Starlette's TestClient, real listeners, real lsof)."""
from __future__ import annotations

import argparse
import io
import json
import os
import stat
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401

import _privacy_oracle as O
import test_h4_fix_is_gated_and_rotates as B   # the builder's fixtures (module only: its tests do not re-run)


def _silent(*_a, **_k):
    pass


class HookTokenIsNeverKeptFromAPlantedRecord(B._Home):
    """AT-2: the running IDE re-reads the bridge record per hook request; a swapped/planted record
    must leave NO usable token (the old one never kept, the planted one never adopted)."""

    def _client(self):
        from starlette.testclient import TestClient
        from mokata.ide.server import AppState, build_app
        return TestClient(build_app(AppState(self.root, "pair", 9999, None, False)),
                          base_url="http://127.0.0.1:9999")

    def _hook(self, client, token):
        return client.post("/api/bus", content=b"{}", headers={"X-Mokata-Hook": token}).status_code

    def _start(self):
        from mokata.ide import bus as B_
        self._setup()
        old = B_.read_bridge(self.root)["token"]
        c = self._client()
        self.assertEqual(204, self._hook(c, old))
        return B_, c, old

    def test_planted_world_readable_record_gives_no_token(self):
        B_, c, old = self._start()
        p = B_.bridge_path(self.root)
        os.unlink(p)
        Path(p).write_text(json.dumps({"port": 1, "token": "PLANTED-TOKEN-0123456789"}), encoding="utf-8")
        O.chmod_like(p, 0o644)
        self.assertEqual(403, self._hook(c, "PLANTED-TOKEN-0123456789"))
        self.assertEqual(403, self._hook(c, old))

    def test_planted_symlinked_record_gives_no_token(self):
        B_, c, old = self._start()
        p = B_.bridge_path(self.root)
        tgt = os.path.join(self._t, "elsewhere.json")
        Path(tgt).write_text(json.dumps({"port": 1, "token": "SYMLINK-TOKEN-0123456789"}), encoding="utf-8")
        os.chmod(tgt, 0o600)
        os.unlink(p)
        os.symlink(tgt, p)
        self.assertEqual(403, self._hook(c, "SYMLINK-TOKEN-0123456789"))
        self.assertEqual(403, self._hook(c, old))

    def test_hardlinked_record_gives_no_token(self):
        B_, c, old = self._start()
        p = B_.bridge_path(self.root)
        os.link(p, os.path.join(self._t, "second-name.json"))       # nlink 2, same bytes
        self.assertEqual(403, self._hook(c, old), "a record with a second name was trusted")

    def test_in_place_rewrite_by_the_owner_with_another_token_is_seen_at_once(self):
        B_, c, old = self._start()
        p = B_.bridge_path(self.root)
        st0 = os.stat(p)
        new = "x" * len(old)
        with open(p, "r+", encoding="utf-8") as fh:               # SAME inode, SAME size
            rec = json.load(fh)
            rec["token"] = new
            fh.seek(0)
            fh.write(json.dumps(rec))
            fh.truncate()
        os.utime(p, ns=(st0.st_atime_ns, st0.st_mtime_ns))        # mtime put back
        self.assertEqual(403, self._hook(c, old), "stale token survived an in-place rewrite")
        self.assertEqual(204, self._hook(c, new))

    def test_record_deleted_gives_no_token(self):
        B_, c, old = self._start()
        os.unlink(B_.bridge_path(self.root))
        self.assertEqual(403, self._hook(c, old))
        self.assertEqual(403, self._hook(c, ""))

    def test_record_made_group_writable_after_start_gives_no_token(self):
        B_, c, old = self._start()
        O.chmod_like(B_.bridge_path(self.root), 0o660)
        self.assertEqual(403, self._hook(c, old))


class TheFixNeverBlessesWhatChangedAfterThePlan(B._Home):
    """AT-3: consent is to the plan that was SHOWN. A hook added to a file after the plan must not be
    kept-and-blessed by the apply (the second yes was never asked about it)."""

    def test_hook_planted_between_plan_and_apply_in_a_weak_writable_file(self):
        from mokata.cli_commands.diagnostics import apply_fix, fix_plan
        from mokata.ide import bus as B_
        self._setup()
        O.grant_others_read(B_.bridge_path(self.root))            # weak bridge -> rotate plan
        O.chmod_like(self.local, 0o666)                               # settings writable by others, no hooks yet
        steps = fix_plan(self.root)
        data = json.loads(Path(self.local).read_text(encoding="utf-8"))
        data["hooks"].setdefault("PreToolUse", []).append(
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "/tmp/late-evil-hook"}]})
        Path(self.local).write_text(json.dumps(data), encoding="utf-8")
        O.chmod_like(self.local, 0o666)
        planted = Path(self.local).read_bytes()
        out = []
        rc = apply_fix(self.root, steps, second_yes=None, out=out.append)   # == --yes
        # H-a round 2 (builder): the apply REFUSES a file that differs from the plan — it never removes
        # the planted hook itself (that would be acting on bytes nobody was shown) and never re-bakes
        # or makes the file private (which would bless the hook). Asserted: untouched, mode kept, named.
        self.assertEqual(1, rc)
        self.assertEqual(planted, Path(self.local).read_bytes(),
                         "a hook that appeared AFTER the plan was shown was rewritten")
        self.assertEqual(0o666, stat.S_IMODE(os.stat(self.local).st_mode),
                         "a hook that appeared AFTER the plan was shown was made private")
        self.assertIn(f"{self.local}: changed since the plan was shown", out)


class YesInsideAnAgentShell(B._Home):
    """AT-P2: note 3a: 'applies only on a human yes at a TTY, or `--yes` OUTSIDE an agent shell'."""

    def test_yes_inside_claudecode_shell_does_not_apply(self):
        from mokata.ide import bus as B_
        self._setup()
        O.grant_others_read(B_.bridge_path(self.root))
        old = B_.read_bridge  # noqa: F841
        before = Path(B_.bridge_path(self.root)).read_bytes()
        rc, out = self._fix(tty=False, env={"CLAUDECODE": "1"})
        self.assertEqual(before, Path(B_.bridge_path(self.root)).read_bytes(),
                         "an agent's own `--yes` rotated the token with no human in the loop")


class RefusedRecordSurfaces(B._Home):
    """AT-R: every surface that can meet a refused server record names it, never a traceback."""

    def _refused_server_record(self):
        from mokata.ide import server as S
        S._write_record(self.root, {"port": 1, "pid": os.getpid(), "token": "t", "root": self.root})
        O.grant_others_read(S.record_path(self.root))
        # H-a round 2 (builder): never a REAL server from this class — under a mutant that lets the
        # refusal through, `open_or_focus` would spawn one that outlives the test (seen twice).
        from mokata.ide import launch
        guard = mock.patch.object(launch, "_open_or_focus",
                                  side_effect=AssertionError("a server was started"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_watch_in_ide_names_a_refused_record(self):
        from mokata.cli_commands import runviews
        self._refused_server_record()
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            try:
                rc = runviews._watch_in_ide(self.root, None, False)
            except Exception as exc:    # noqa: BLE001
                self.fail(f"`mokata watch` raised {type(exc).__name__}: {exc}")
        self.assertNotEqual(0, rc)
        self.assertIn("refused", err.getvalue())

    def test_review_window_hook_says_it(self):
        from mokata import hook_cli
        self._refused_server_record()
        line = hook_cli._ide_for_prompt(self.root, "/mokata:review-window", "s1")
        self.assertIn("refused", line or "")


class PlainDoctorWithAWeakBridge(B._Home):
    """AT-D: the command the refusal line tells the user to run first must not traceback."""

    # H-a merge with wave 2: G's D9 fix is in (narrowed to RecordRefused); was `expectedFailure`.
    def test_doctor_cli_names_the_weak_bridge_with_http_gates_wired(self):
        import subprocess
        from mokata.ide import bus as B_
        self._setup()
        O.grant_others_read(B_.bridge_path(self.root))
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, PYTHONPATH=os.path.join(os.path.dirname(__file__), "..", "src"))
        env.pop("CLAUDECODE", None)
        r = subprocess.run([sys.executable, "-m", "mokata", "doctor", "--path", self.root], env=env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        self.assertNotIn("Traceback", r.stderr + r.stdout, (r.stdout + r.stderr)[-1500:])
        # r2: the round-1 call passed the path positionally, which argparse rejects (rc 2, a usage
        # error), so "no Traceback" was vacuous. It must actually run the doctor now.
        self.assertNotEqual(2, r.returncode, (r.stdout + r.stderr)[-800:])


class RotationFromARecordOthersWrote(B._Home):
    """AT-1b: a weak record whose port is planted must not keep the port (the new token would be
    baked into URLs for it) - variants: nlink 2, group-writable, ACL-free 0666."""

    def _port_after_fix(self, mutate):
        from mokata.ide import bus as B_
        self._setup()
        p = B_.bridge_path(self.root)
        rec = json.loads(Path(p).read_text(encoding="utf-8"))
        rec["port"] = 31337
        Path(p).write_text(json.dumps(rec), encoding="utf-8")
        mutate(p)
        rc, out = self._fix()
        return B_.read_bridge(self.root)["port"], out

    def test_group_writable_port_dropped(self):
        port, out = self._port_after_fix(lambda p: O.chmod_like(p, 0o664))
        self.assertNotEqual(31337, port, out)

    def test_hardlinked_port_dropped(self):
        port, out = self._port_after_fix(lambda p: (O.chmod_like(p, 0o644),
                                                    os.link(p, p + ".other")))
        self.assertNotEqual(31337, port, out)

    def test_world_writable_port_dropped(self):
        port, out = self._port_after_fix(lambda p: O.chmod_like(p, 0o666))
        self.assertNotEqual(31337, port, out)


class HardLinkedSettingsCopy(B._Home):
    """AT-hl: a worktree copy that is a hard link of the repo's settings file must be rewritten
    without writing through the shared inode (the other name keeps the OLD bytes)."""

    def test_rebake_replaces_never_writes_through(self):
        from mokata.ide import bus as B_
        files = self._setup(copies=1)
        copy = files[1]
        os.unlink(copy)
        os.link(self.local, copy)
        old = B_.read_bridge(self.root)["token"]
        O.grant_others_read(B_.bridge_path(self.root))
        rc, out = self._fix()
        for p in files:
            self.assertNotIn(old, Path(p).read_text(encoding="utf-8"), p)
            self.assertEqual(1, os.lstat(p).st_nlink, f"{p} still shares an inode")


if __name__ == "__main__":
    unittest.main()
