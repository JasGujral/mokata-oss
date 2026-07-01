"""Stage 54b — always-visible pipeline-stage indicator (mode-badge UX).

A persistent "mode badge" — the feel of Claude Code's own "plan mode on" indicator — that
always shows which user-facing stage the run is in (brainstorm -> spec -> develop -> review
-> ship, active one highlighted), surfaced INSIDE Claude Code via mokata's own statusLine.

These tests assert:
  * `progress.build_stage_badge(surface)` highlights the stage derived from run-state and
    degrades to a minimal `mokata` with no run; it is deterministic + read-only;
  * the `mokata-hook statusline` subcommand prints the badge (honoring the stdin cwd +
    session_name), prints NOTHING when settings.ux.statusline=false, composes a pre-existing
    user statusline via `--wrap`, and ALWAYS exits 0 (even on garbage / EOF stdin);
  * `setup` wires mokata's statusLine default-on and merge-safe (a user's statusLine is
    preserved/composed, not clobbered); the toggle + `unsetup` remove/restore it.

Any test exercising the statusline command feeds an EOF/closed stdin so it never blocks (the
53b lesson).
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR, hook_cli, progress
from mokata import harness_setup
from mokata.config import Surface
from mokata.govern import PipelineCheckpoint
from mokata.brainstorm import Approach, BrainstormSession, save_brainstorm_progress

PHASES = ("brainstorm", "analysis", "strawman", "pre_mortem", "probes",
          "completeness_gate", "emit")


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _in_progress_brainstorm():
    s = BrainstormSession("slugify")
    s.ask("unicode or ascii-only?")
    s.answer("ascii-only")
    s.propose_approaches([
        Approach("regex", "strip via regex", pros=["tiny"], cons=["edge cases"]),
        Approach("library", "use a slug lib", pros=["robust"], cons=["a dependency"]),
    ])
    return s  # deliberately NOT approved — mid-stream


def _set_manifest_statusline(root, value):
    """Flip settings.ux.statusline directly in the committed manifest."""
    mpath = Path(root) / MOKATA_DIR / "manifest.json"
    data = json.loads(mpath.read_text(encoding="utf-8"))
    data.setdefault("settings", {}).setdefault("ux", {})["statusline"] = value
    mpath.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ====================================================================== build_stage_badge
class TestBuildStageBadge(unittest.TestCase):
    def test_no_run_degrades_to_minimal(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            self.assertEqual(progress.build_stage_badge(surface), "mokata")

    def test_brainstorm_in_progress_highlighted(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            save_brainstorm_progress(_in_progress_brainstorm(), surface.state)
            badge = progress.build_stage_badge(surface)
            self.assertIn("›brainstorm‹", badge)       # ›brainstorm‹
            self.assertNotIn("›spec‹", badge)
            self.assertTrue(badge.startswith("mokata"))

    def test_spec_stage_highlighted_with_counter(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").mark_passed("brainstorm")
            badge = progress.build_stage_badge(surface)
            self.assertIn("›spec‹", badge)             # ›spec‹
            self.assertIn("1/7", badge)                          # phase counter

    def test_develop_highlighted_when_pipeline_complete(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            cp = PipelineCheckpoint(surface.state, "run-a")
            for p in PHASES:
                cp.mark_passed(p)
            badge = progress.build_stage_badge(surface)
            self.assertIn("›develop‹", badge)          # ›develop‹

    def test_session_name_hook(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").mark_passed("brainstorm")
            badge = progress.build_stage_badge(surface, session_name="auth-refactor")
            self.assertIn("auth-refactor", badge)

    def test_ascii_mode(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").mark_passed("brainstorm")
            badge = progress.build_stage_badge(surface, ascii_only=True)
            self.assertIn(">spec<", badge)
            self.assertNotIn("›", badge)

    def test_deterministic_and_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").mark_passed("brainstorm")
            state_root = surface.state.root
            before = sorted(os.listdir(state_root))
            first = progress.build_stage_badge(surface)
            second = progress.build_stage_badge(surface)
            after = sorted(os.listdir(state_root))
            self.assertEqual(first, second)        # deterministic
            self.assertEqual(before, after)        # read-only — no new state written

    def test_broken_surface_never_raises(self):
        class Boom:
            @property
            def state(self):
                raise RuntimeError("no state")

            @property
            def manifest(self):
                raise RuntimeError("no manifest")
        # must degrade, never raise
        self.assertEqual(progress.build_stage_badge(Boom()), "mokata")


# ===================================================================== statusline command
@contextlib.contextmanager
def _stdin(text):
    old = sys.stdin
    sys.stdin = io.StringIO(text)
    try:
        yield
    finally:
        sys.stdin = old


def _run_statusline(stdin_text, argv=None):
    out = io.StringIO()
    with _stdin(stdin_text), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(io.StringIO()):
        rc = hook_cli.statusline_main(argv or [])
    return rc, out.getvalue()


class TestStatuslineCommand(unittest.TestCase):
    def test_prints_badge_for_active_run(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").mark_passed("brainstorm")
            payload = json.dumps({"workspace": {"current_dir": d}})
            rc, out = _run_statusline(payload)
            self.assertEqual(rc, 0)
            self.assertIn("mokata", out)
            self.assertIn("›spec‹", out)

    def test_honors_session_name_from_payload(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").mark_passed("brainstorm")
            payload = json.dumps({"cwd": d, "session_name": "auth-refactor"})
            rc, out = _run_statusline(payload)
            self.assertEqual(rc, 0)
            self.assertIn("auth-refactor", out)

    def test_prints_nothing_when_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").mark_passed("brainstorm")
            _set_manifest_statusline(d, False)
            rc, out = _run_statusline(json.dumps({"cwd": d}))
            self.assertEqual(rc, 0)
            self.assertEqual(out, "")

    def test_uninitialized_cwd_prints_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = _run_statusline(json.dumps({"cwd": d}))
            self.assertEqual(rc, 0)
            self.assertEqual(out, "")

    def test_garbage_stdin_exits_zero(self):
        rc, _ = _run_statusline("this is not json at all")
        self.assertEqual(rc, 0)

    def test_eof_stdin_never_blocks(self):
        rc, _ = _run_statusline("")
        self.assertEqual(rc, 0)

    def test_composes_wrapped_user_statusline(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").mark_passed("brainstorm")
            payload = json.dumps({"cwd": d})
            rc, out = _run_statusline(payload, ["--wrap", "printf 'USERLINE'"])
            self.assertEqual(rc, 0)
            self.assertIn("USERLINE", out)        # their statusline ran
            self.assertIn("mokata", out)          # and mokata's badge was appended

    def test_wrapped_user_line_survives_mokata_disabled(self):
        # turning mokata's badge OFF must NOT break the user's own statusline
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            _set_manifest_statusline(d, False)
            rc, out = _run_statusline(json.dumps({"cwd": d}), ["--wrap", "printf 'USERLINE'"])
            self.assertEqual(rc, 0)
            self.assertIn("USERLINE", out)
            self.assertNotIn("›", out)       # no mokata badge segment

    def test_dispatch_via_main(self):
        with _stdin(""), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(hook_cli.main(["statusline"]), 0)


# ======================================================================== setup wiring
def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class TestSetupWiring(unittest.TestCase):
    def test_merge_statusline_default_on(self):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "settings.json"
            harness_setup._merge_statusline(sp)
            data = _read_json(sp)
            cmd = data["statusLine"]["command"]
            self.assertIn("mokata-hook", cmd)
            self.assertIn("statusline", cmd)
            self.assertEqual(data["statusLine"]["type"], "command")

    def test_merge_safe_preserves_user_statusline(self):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "settings.json"
            sp.write_text(json.dumps(
                {"statusLine": {"type": "command", "command": "my-custom-line"}}),
                encoding="utf-8")
            harness_setup._merge_statusline(sp)
            data = _read_json(sp)
            cmd = data["statusLine"]["command"]
            self.assertIn("mokata-hook", cmd)
            self.assertIn("--wrap", cmd)
            self.assertIn("my-custom-line", cmd)            # composes the user's command
            # original preserved verbatim for a clean restore
            self.assertEqual(
                data["statusLine"][harness_setup._WRAPPED_KEY]["command"], "my-custom-line")

    def test_idempotent_resetup_keeps_single_wrap(self):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "settings.json"
            sp.write_text(json.dumps(
                {"statusLine": {"type": "command", "command": "my-custom-line"}}),
                encoding="utf-8")
            harness_setup._merge_statusline(sp)
            harness_setup._merge_statusline(sp)          # run twice
            data = _read_json(sp)
            wrapped = data["statusLine"][harness_setup._WRAPPED_KEY]
            self.assertEqual(wrapped["command"], "my-custom-line")   # not double-wrapped
            self.assertNotIn(harness_setup._WRAPPED_KEY, json.dumps(wrapped))

    def test_apply_setup_wires_statusline(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata.harness_setup import setup_harness
            setup_harness("claude", root=d, scope="project",
                          assume_yes=True, out=lambda _: None)
            data = _read_json(Path(d) / ".claude" / "settings.json")
            self.assertIn("statusLine", data)
            self.assertIn("mokata-hook", data["statusLine"]["command"])

    def test_unsetup_restores_user_statusline(self):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / ".claude" / "settings.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(json.dumps(
                {"statusLine": {"type": "command", "command": "my-custom-line"}}),
                encoding="utf-8")
            from mokata.harness_setup import setup_harness, unsetup_harness
            setup_harness("claude", root=d, scope="project",
                          assume_yes=True, out=lambda _: None)
            self.assertIn("mokata-hook", _read_json(sp)["statusLine"]["command"])
            unsetup_harness("claude", root=d, scope="project",
                            assume_yes=True, out=lambda _: None)
            restored = _read_json(sp)["statusLine"]
            self.assertEqual(restored, {"type": "command", "command": "my-custom-line"})

    def test_unsetup_removes_pure_mokata_statusline(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata.harness_setup import setup_harness, unsetup_harness
            setup_harness("claude", root=d, scope="project",
                          assume_yes=True, out=lambda _: None)
            sp = Path(d) / ".claude" / "settings.json"
            self.assertIn("statusLine", _read_json(sp))
            unsetup_harness("claude", root=d, scope="project",
                            assume_yes=True, out=lambda _: None)
            self.assertNotIn("statusLine", _read_json(sp))

    def test_setup_respects_disabled_setting(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)                              # pre-init so setup won't re-init
            _set_manifest_statusline(d, False)
            from mokata.harness_setup import setup_harness
            setup_harness("claude", root=d, scope="project",
                          assume_yes=True, out=lambda _: None)
            data = _read_json(Path(d) / ".claude" / "settings.json")
            self.assertNotIn("statusLine", data)   # opt-out honored


if __name__ == "__main__":
    unittest.main()
