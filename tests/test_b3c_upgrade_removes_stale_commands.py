"""0.0.23 B3c — re-running `mokata setup claude` after an upgrade leaves exactly THIS mokata's commands.

Found while testing the install/update path (Jas 2026-10-15: *"make sure to test that installation
and update show commands like /review-window inside the Claude Code session"*): setup wrote the new
names (`mokata-review.md`, `review-window.md`) but kept the old ones (`review.md` — still shadowed by
Claude Code's built-in `/review` — and 0.0.22-pre `ide.md`). A user's OWN command files carry no
`mokata ·` mark and are never touched.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup

_MOKATA = b"---\nname: review\ndescription: mokata \xc2\xb7 old review\n---\n# body\n"
_THEIRS = b"---\ndescription: my team's review checklist\n---\n# mine\n"


class StaleCommands(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "repo"
        self.home = Path(tmp.name) / "home"
        self.root.mkdir()
        self.home.mkdir()
        self.plan = harness_setup.plan_setup("claude", root=str(self.root), home=str(self.home))
        self.cdir = Path(str(self.plan.targets.commands_dir))
        self.cdir.mkdir(parents=True)

    def test_renamed_mokata_commands_are_found(self):
        (self.cdir / "review.md").write_bytes(_MOKATA)
        (self.cdir / "ide.md").write_bytes(_MOKATA.replace(b"review", b"ide"))
        names = [p.name for p in harness_setup.stale_command_files(
            "claude", self.cdir, self.plan.command_files)]
        self.assertEqual(names, ["ide.md", "review.md"])

    def test_a_users_own_command_is_never_touched(self):
        (self.cdir / "review.md").write_bytes(_THEIRS)
        (self.cdir / "notes.md").write_bytes(b"no frontmatter at all\n")
        self.assertEqual(harness_setup.stale_command_files(
            "claude", self.cdir, self.plan.command_files), [])

    def test_current_commands_are_never_stale(self):
        for n in self.plan.command_files:
            src = harness_setup._templates_dir() / n
            (self.cdir / harness_setup._command_target_name("claude", n)).write_bytes(src.read_bytes())
        self.assertEqual(harness_setup.stale_command_files(
            "claude", self.cdir, self.plan.command_files), [])

    def test_apply_setup_removes_them_and_writes_review_window(self):
        (self.cdir / "review.md").write_bytes(_MOKATA)
        (self.cdir / "mine.md").write_bytes(_THEIRS)
        harness_setup.apply_setup(self.plan, assume_yes=True, out=lambda s: None)
        names = {p.name for p in self.cdir.iterdir()}
        self.assertNotIn("review.md", names)
        self.assertIn("mokata-review.md", names)
        self.assertIn("review-window.md", names)
        self.assertIn("mine.md", names)


if __name__ == "__main__":
    unittest.main()
