"""B-SKILLS — new-session skills-menu bug: ground, fix, make doctor name it.

Live report (Jas 2026-07-17): opening a NEW session on the same repo, the `/` command menu shows no
mokata skills (the first session did). Phase A pinned the failure STATE: the session's root lacks
the curated `.claude/skills/` + `.claude/commands/` — a git worktree (setup writes to the literal
root, never the canonical checkout), a fresh/second checkout, or a project-scoped install viewed
from a non-setup root. The SessionStart hook is NOT involved (skills are durable files written by
`mokata setup`, read from disk per session, not installed by any hook).

mokata cannot install skills into someone else's session, so the fix is LEGIBILITY (P16/P22): a
`mokata doctor` skills-visibility finding + a new-session briefing offer that NAME why the skills
aren't visible and the one command that wires them (`mokata setup claude`), with the restart hint
every loud finding carries (Claude Code caches the skill/command list per session — the answer to
the pure CC-caching case). Named finding per the B-VER pattern (`*Finding`, LOUD only when wrong,
never raises).

Business-level asserts throughout: on the rendered text the approving human actually sees.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import argparse
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import skills_visibility as SV                        # noqa: E402
from mokata.agent_skills import installed_skill_names             # noqa: E402
from mokata.bootstrap import build_bootstrap                      # noqa: E402
from mokata.config import Surface                                 # noqa: E402
from mokata.harness_setup import apply_setup, plan_setup          # noqa: E402
from mokata.init import init_repo                                 # noqa: E402
from mokata.skills_visibility import (SkillsVisibilityFinding,    # noqa: E402
                                      briefing_offer, skills_visibility,
                                      skills_visibility_lines)


def _init(root: str) -> None:
    init_repo(root=root, profile="standard", assume_yes=True, out=lambda *_a: None)


def _wire(root: str, home: str) -> None:
    """Fully wire the claude harness into `root` (init + skills + commands + mcp/settings), the way
    a real `mokata setup claude` does — via plan+apply (no CONNECTED/parity subprocess probe)."""
    plan = plan_setup("claude", root=root, home=home)
    apply_setup(plan, assume_yes=True, out=lambda *_a: None)


class TestMissingBranch(unittest.TestCase):
    """A root with mokata initialized but NO `.claude/` wiring — the fresh/second-checkout repro."""

    def test_status_missing_and_names_the_fix(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _init(root)
            f = skills_visibility(root, home=home)
            self.assertEqual(f.status, "missing")
            self.assertEqual(f.present_skills, 0)
            self.assertEqual(f.total_skills, len(installed_skill_names()))
            out = "\n".join(f.render(quiet_when_ok=False))
            self.assertIn("NOT VISIBLE", out)
            self.assertIn("mokata setup claude", out)
            # the restart hint rides every loud finding (the CC-caching answer)
            self.assertIn("restart Claude Code", out)

    def test_missing_is_loud_even_when_quiet(self):
        # LOUD-only means a PRESENT repo is quiet — but a broken one speaks even on the write paths.
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _init(root)
            self.assertTrue(skills_visibility(root, home=home).render(quiet_when_ok=True))


class TestWorktreeBranch(unittest.TestCase):
    """A worktree root (separate from the main checkout) names itself as a worktree — the primary
    mokata-wiring cause Phase A confirmed."""

    def test_worktree_missing_names_worktree(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _init(root)
            # worktree_label is imported inside skills_visibility, so patching the source resolves.
            with mock.patch("mokata.repo_identity.worktree_label", return_value="wt/feature-x"):
                f = skills_visibility(root, home=home)
            self.assertEqual(f.status, "missing")
            self.assertTrue(f.is_worktree)
            out = "\n".join(f.render(quiet_when_ok=False))
            self.assertIn("git worktree", out)
            self.assertIn("wt/feature-x", out)
            self.assertIn("mokata setup claude", out)


class TestPresentBranch(unittest.TestCase):
    """A fully-wired root — doctor is QUIET on the write paths and shows a `visible ✓ + restart`
    line on the doctor path (the healthy-repo negative)."""

    def test_present_quiet_on_write_paths(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _wire(root, home)
            f = skills_visibility(root, home=home)
            self.assertEqual(f.status, "present")
            self.assertEqual(f.missing_skills, [])
            self.assertEqual(f.missing_commands, [])
            # QUIET on the write paths (byte-identical setup output)
            self.assertEqual(f.render(quiet_when_ok=True), [])

    def test_present_shows_visible_line_on_doctor(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _wire(root, home)
            out = "\n".join(skills_visibility(root, home=home).render(quiet_when_ok=False))
            self.assertIn("visible ✓", out)
            self.assertIn("restart Claude Code", out)


class TestPartialBranch(unittest.TestCase):
    """A wired root with a skill dir removed — an incomplete/stale install, named as PARTIAL."""

    def test_partial_names_the_missing_skill(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _wire(root, home)
            victim = installed_skill_names()[0]
            import shutil
            shutil.rmtree(Path(root) / ".claude" / "skills" / victim)
            f = skills_visibility(root, home=home)
            self.assertEqual(f.status, "partial")
            self.assertIn(victim, f.missing_skills)
            out = "\n".join(f.render(quiet_when_ok=False))
            self.assertIn("PARTIAL/STALE", out)
            self.assertIn(victim, out)
            self.assertIn("re-run `mokata setup claude`", out)


class TestPluginShadowNote(unittest.TestCase):
    """A mokata plugin present alongside setup — the B-VER shadow sweep, noted on the finding."""

    def test_missing_with_plugin_notes_the_plugin(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _init(root)
            fake = mock.Mock(plugin_root=Path("/some/plugin/root"))
            with mock.patch("mokata.skills_visibility._plugin_shadow", return_value=fake):
                f = skills_visibility(root, home=home)
            self.assertTrue(f.plugin_present)
            out = "\n".join(f.render(quiet_when_ok=False))
            self.assertIn("plugin is installed", out)
            # The finding renders the root with `str(Path)`, which is NATIVE-separated — so the
            # expectation is built the same way rather than hardcoding a POSIX separator (on
            # Windows the line reads `\some\plugin\root`).
            self.assertIn(str(Path("/some/plugin/root")), out)


class TestUncheckableNeverRaises(unittest.TestCase):
    """A read-only diagnostic that must NEVER crash the doctor — a broken check degrades to one
    honest line, never an exception."""

    def test_uncheckable_when_internals_raise(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _init(root)
            with mock.patch("mokata.agent_skills.installed_skill_names",
                            side_effect=RuntimeError("boom")):
                f = skills_visibility(root, home=home)   # must not raise
            self.assertEqual(f.status, "uncheckable")
            out = "\n".join(f.render(quiet_when_ok=False))
            self.assertIn("could not check", out)

    def test_lines_helper_never_raises(self):
        with mock.patch("mokata.skills_visibility.skills_visibility",
                        side_effect=RuntimeError("nope")):
            lines = skills_visibility_lines(".")            # must not raise
        self.assertTrue(any("skipped" in ln for ln in lines))


class TestBriefingOffer(unittest.TestCase):
    """The SessionStart detect-and-OFFER (WT.S1 pattern): a new session on an un-wired root SAYS
    why the `/` menu is empty; a wired root gets no offer (byte-identical briefing)."""

    def test_offer_present_when_missing(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _init(root)
            offer = briefing_offer(root, home=home)
            self.assertIsNotNone(offer)
            self.assertIn("mokata setup claude", offer)
            self.assertIn("restart Claude Code", offer)

    def test_no_offer_when_wired(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _wire(root, home)
            self.assertIsNone(briefing_offer(root, home=home))

    def test_bootstrap_appends_offer_when_present(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _init(root)
            surface = Surface.load(root)
            with mock.patch("mokata.skills_visibility.briefing_offer",
                            return_value="⚠ OFFER-SENTINEL"):
                text = build_bootstrap(surface).text
            self.assertIn("OFFER-SENTINEL", text)

    def test_bootstrap_unchanged_when_no_offer(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _init(root)
            surface = Surface.load(root)
            with mock.patch("mokata.skills_visibility.briefing_offer", return_value=None):
                text = build_bootstrap(surface).text
            self.assertNotIn("won't show mokata's skills", text)


class TestSetupIdempotence(unittest.TestCase):
    """Idempotence pin: setup on an already-correct root re-writes the skills + commands
    BYTE-IDENTICALLY (a re-run never churns the files it just wrote)."""

    @staticmethod
    def _snapshot(root: str) -> dict:
        base = Path(root) / ".claude"
        snap = {}
        for sub in ("skills", "commands"):
            d = base / sub
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    snap[str(p.relative_to(base))] = p.read_bytes()
        return snap

    def test_second_setup_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _wire(root, home)
            first = self._snapshot(root)
            _wire(root, home)              # re-run on the already-correct root
            second = self._snapshot(root)
            self.assertEqual(first, second)
            self.assertTrue(first)         # guard: we actually snapshotted files


class TestBSkillsRegression(unittest.TestCase):
    """THE regression, at the filesystem level: construct the repro state Phase A pinned (a root
    with mokata initialized but no `.claude/` wiring — a second checkout / worktree) and prove the
    user-visible surfaces now NAME it. Pre-stage `mokata doctor` said nothing about skills on such a
    root (and the module didn't exist), so this fails on pre-stage code."""

    def test_doctor_names_the_missing_skills(self):
        from mokata.cli_commands.diagnostics import cmd_doctor
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _init(root)                     # initialized, but NOT set up → `.claude/skills` absent
            self.assertFalse((Path(root) / ".claude" / "skills").exists())
            args = argparse.Namespace(path=root, home=home, matrix=False)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cmd_doctor(args)       # doctor must not crash on this root
            out = buf.getvalue()
            self.assertIn("NOT VISIBLE", out)
            self.assertIn("mokata setup claude", out)
            # the finding is informational — it never flips doctor's exit (manifest is healthy)
            self.assertEqual(rc, 0)

    def test_wired_root_doctor_quiet_then_visible(self):
        # The other side of the regression: once wired, doctor stops shouting and confirms visible.
        from mokata.cli_commands.diagnostics import cmd_doctor
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            _wire(root, home)
            args = argparse.Namespace(path=root, home=home, matrix=False)
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_doctor(args)
            out = buf.getvalue()
            self.assertNotIn("NOT VISIBLE", out)
            self.assertIn("visible ✓", out)


if __name__ == "__main__":
    unittest.main()
