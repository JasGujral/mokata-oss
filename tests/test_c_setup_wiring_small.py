"""0.0.29 lane C1 small rows: INIT-WIRES-FIRST-PATH-INSTALL.

What setup wires must be the install that RAN it. A second `mokata-*` earlier on PATH (a stale
pipx/uv tool, a system pip) used to win over the venv that is executing the command."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import harness_setup as HS

NAME = "mokata-hook"


def _exe(directory, name=NAME, ext=""):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / (name + ext)
    p.write_text("#!/bin/sh\n", encoding="utf-8")
    p.chmod(0o755)
    return p


class TheWiringIsTheInstallThatRan(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    @staticmethod
    def _which(name):
        # shutil.which on posix does not honour PATHEXT, so a Windows `.bat` shim is found by
        # asking the PATH dirs for it explicitly
        for d in os.environ.get("PATH", "").split(os.pathsep):
            for ext in ("", ".bat", ".cmd"):
                if d and os.path.isfile(os.path.join(d, name + ext)):
                    return os.path.join(d, name + ext)
        return None

    def _resolve(self, interpreter, path_dirs, *, scripts=""):
        env = {"PATH": os.pathsep.join(str(p) for p in path_dirs)}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, "executable", str(interpreter)), \
                mock.patch("sysconfig.get_path", return_value=scripts), \
                mock.patch.object(HS.shutil, "which", side_effect=self._which):
            return HS.resolved_console_script(NAME)

    def test_a_decoy_earlier_on_path_loses_to_the_interpreters_own_install(self):
        venv_bin, decoy_bin = self.tmp / "venv" / "bin", self.tmp / "decoy"
        mine = _exe(venv_bin)
        _exe(decoy_bin)
        python = _exe(venv_bin, "python")
        self.assertEqual(str(mine), self._resolve(python, [decoy_bin, venv_bin]))

    def test_the_interpreter_is_not_resolved_through_its_symlink(self):
        # a venv's python is a symlink into the base install: resolve() would leave the venv
        base_bin, venv_bin = self.tmp / "base" / "bin", self.tmp / "venv" / "bin"
        base_py = _exe(base_bin, "python")
        mine = _exe(venv_bin)
        _exe(base_bin)                       # a console script beside the REAL interpreter too
        venv_bin.mkdir(parents=True, exist_ok=True)
        link = venv_bin / "python"
        os.symlink(base_py, link)
        self.assertEqual(str(mine), self._resolve(link, []))

    def test_the_scripts_dir_comes_before_path(self):
        scripts, decoy_bin = self.tmp / "scripts", self.tmp / "decoy"
        py = _exe(self.tmp / "emptybin", "python")
        mine = _exe(scripts)
        _exe(decoy_bin)
        self.assertEqual(str(mine), self._resolve(py, [decoy_bin], scripts=str(scripts)))

    def test_sibling_exe_suffix_is_found(self):
        venv_bin = self.tmp / "venv" / "Scripts"
        py = _exe(venv_bin, "python", ".exe")
        mine = _exe(venv_bin, NAME, ".exe")
        self.assertEqual(str(mine), self._resolve(py, []))

    def test_a_windows_shim_is_the_last_resort_after_a_real_install(self):
        venv_bin, shims = self.tmp / "venv" / "bin", self.tmp / "shims"
        py = _exe(venv_bin, "python")
        shim = _exe(shims, NAME, ".bat")
        # no real install anywhere: the shim beats a bare name
        self.assertEqual(str(shim), self._resolve(py, [shims]))
        # a real sibling beats the shim
        mine = _exe(venv_bin)
        self.assertEqual(str(mine), self._resolve(py, [shims]))

    def test_path_is_still_used_when_the_interpreter_has_no_install(self):
        py = _exe(self.tmp / "emptybin", "python")
        found = _exe(self.tmp / "onpath")
        self.assertEqual(str(found), self._resolve(py, [self.tmp / "onpath"]))

    def test_nothing_anywhere_is_the_bare_name(self):
        py = _exe(self.tmp / "emptybin", "python")
        self.assertEqual(NAME, self._resolve(py, [self.tmp / "nowhere"]))


class UnwireRemovesOnlyDirectoriesWireOwns(unittest.TestCase):
    """The emptied-directory sweep is Claude's `.claude/commands` + `.claude`. Another harness's
    commands parent is a directory the user owns (copilot's is `.github`): never removed."""

    def test_an_empty_github_dir_survives_a_copilot_unwire(self):
        from mokata.harness_setup import setup_harness, unsetup_harness
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".github"))
            quiet = lambda _: None  # noqa: E731
            setup_harness("copilot", root=d, assume_yes=True, out=quiet)
            self.assertTrue(os.listdir(os.path.join(d, ".github")), "copilot wire wrote nothing")
            unsetup_harness("copilot", root=d, assume_yes=True, out=quiet)
            self.assertTrue(os.path.isdir(os.path.join(d, ".github")))


class SettingsJsonKeepsItsLineEndings(unittest.TestCase):
    """Wire then unwire leaves `.claude/settings.json` byte-identical when it is in Claude Code's own
    2-space format — on every OS. The mirror's Windows cell found it coming back CRLF: the merge
    was written through the platform's newline translation. The file's own line ending is kept."""

    def _round_trip(self, original: bytes) -> bytes:
        from mokata.harness_setup import setup_harness, unsetup_harness
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".claude", "settings.json")
            os.makedirs(os.path.dirname(p))
            with open(p, "wb") as fh:
                fh.write(original)
            quiet = lambda _: None  # noqa: E731
            setup_harness("claude", root=d, assume_yes=True, out=quiet)
            with open(p, "rb") as fh:
                wired = fh.read()
            unsetup_harness("claude", root=d, assume_yes=True, out=quiet)
            with open(p, "rb") as fh:
                after = fh.read()
        self.assertNotEqual(original, wired, "setup did not merge into settings.json")
        return after

    def test_lf_stays_lf(self):
        original = b'{\n  "model": "opus"\n}\n'
        self.assertEqual(original, self._round_trip(original))

    def test_crlf_stays_crlf(self):
        original = b'{\r\n  "model": "opus"\r\n}\r\n'
        self.assertEqual(original, self._round_trip(original))


if __name__ == "__main__":
    unittest.main()
