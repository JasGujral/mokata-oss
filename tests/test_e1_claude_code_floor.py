"""0.0.22 stage E1 — the minimum Claude Code version, derived and checked.

The floor is the newest of the hook events mokata depends on (`claude_code_version.FEATURES`);
`mokata doctor` names each feature that is off on an older Claude Code, and says nothing when
Claude Code is absent (another harness) or new enough.
"""
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import claude_code_version as CC
from mokata.config import Surface
from mokata.govern.doctor import diagnose
from mokata.init import init_repo


class TheFloorIsDerived(unittest.TestCase):

    def test_the_floor_is_the_newest_dependency_never_typed_beside_it(self):
        self.assertEqual(max(v for _f, v, _l in CC.FEATURES), CC.MIN_VERSION)
        self.assertEqual((2, 1, 198), CC.MIN_VERSION)   # P31 session worktrees; L3 manifest-derived

    def test_versions_parse_from_what_claude_prints(self):
        self.assertEqual((2, 1, 290), CC.parse("2.1.290 (Claude Code)"))
        self.assertIsNone(CC.parse("command not found"))

    def test_an_older_claude_code_names_exactly_what_it_loses(self):
        self.assertEqual(["behavior.tool-hooks-in-subagents", "field.last_assistant_message",
                          "field.tool-hook.agent_id", "behavior.worktree-session-resume"],
                         CC.missing_features((2, 0, 50)))
        self.assertEqual(9, len(CC.missing_features((2, 0, 0))))
        self.assertEqual([], CC.missing_features(CC.MIN_VERSION))


class TheDoctorSaysIt(unittest.TestCase):

    def _report(self, version):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
            with mock.patch.object(CC, "installed_version", return_value=version):
                return diagnose(Surface.load(d))

    def _codes(self, report):
        return [f.code for f in report.findings]

    def test_too_old_is_a_warning_naming_the_features(self):
        report = self._report((2, 0, 43))
        [f] = [f for f in report.findings if f.code == "claude-code-too-old"]
        self.assertEqual("warning", f.severity)
        self.assertIn("behavior.tool-hooks-in-subagents", f.detail)
        self.assertIn("claude update", f.detail)

    def test_new_enough_or_absent_is_silent(self):
        for v in (CC.MIN_VERSION, (2, 1, 290), None):
            with self.subTest(version=v):
                self.assertNotIn("claude-code-too-old", self._codes(self._report(v)))


if __name__ == "__main__":
    unittest.main()
