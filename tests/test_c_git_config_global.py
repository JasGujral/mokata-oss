"""0.0.29 C1 — `GIT-CONFIG-GLOBAL-WRONG-FILE` and `GIT-CONTROL-UNDOCUMENTED`.

A refused `git config --global|--system core.hooksPath` names the file git REALLY writes (never this
repository's config), with its own reason; the gated set is otherwise unchanged and no longer
depends on cwd being inside a repository. `docs/security.md` quotes every `HONEST_BOUNDS` entry.
Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import git_control as GC

ROOT = Path(__file__).resolve().parents[1]


def _repo(d: str) -> str:
    subprocess.run(["git", "init", "-q", d], check=True)
    return os.path.realpath(d)


class TheGlobalHooksPathNamesTheRealFile(unittest.TestCase):
    def setUp(self):
        t = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(t, ignore_errors=True))
        self.home = os.path.join(t, "home")
        os.makedirs(self.home)
        self.repo = _repo(os.path.join(t, "repo"))
        self.outside = os.path.join(t, "plain")
        os.makedirs(self.outside)

    def env(self, **kw):
        base = {"HOME": self.home, "USERPROFILE": self.home}
        base.update(kw)
        return mock.patch.dict(os.environ, base, clear=False)

    def writes(self, cmd, cwd=None):
        return GC.git_config_writes(cmd, cwd or self.repo)

    def test_global_names_home_gitconfig_with_its_own_reason(self):
        with self.env(), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GIT_CONFIG_GLOBAL", None)
            os.environ.pop("XDG_CONFIG_HOME", None)
            got = self.writes("git config --global core.hooksPath /tmp/evil")
        self.assertEqual([(os.path.join(self.home, ".gitconfig"), GC.GLOBAL_HOOKSPATH)], got)
        self.assertIn("EVERY repository", GC._WHY[GC.GLOBAL_HOOKSPATH])

    def test_git_config_global_env_wins(self):
        target = os.path.join(self.home, "elsewhere.cfg")
        with self.env(), mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": target}):
            got = self.writes("git config --global core.hooksPath /x")
        self.assertEqual([(target, GC.GLOBAL_HOOKSPATH)], got)

    def test_xdg_when_only_it_exists_and_home_gitconfig_when_both(self):
        xdg = os.path.join(self.home, "xdg")
        os.makedirs(os.path.join(xdg, "git"))
        Path(xdg, "git", "config").write_text("", encoding="utf-8")
        with self.env(XDG_CONFIG_HOME=xdg):
            os.environ.pop("GIT_CONFIG_GLOBAL", None)
            self.assertEqual(os.path.join(xdg, "git", "config"),
                             self.writes("git config --global core.hooksPath /x")[0][0])
            Path(self.home, ".gitconfig").write_text("", encoding="utf-8")
            self.assertEqual(os.path.join(self.home, ".gitconfig"),
                             self.writes("git config --global core.hooksPath /x")[0][0])

    def test_system_names_the_system_file(self):
        with self.env(), mock.patch.dict(os.environ, {"GIT_CONFIG_SYSTEM": "/opt/git/sys.cfg"}):
            got = self.writes("git config --system core.hooksPath /x")
        self.assertEqual([(os.path.normpath("/opt/git/sys.cfg"), GC.GLOBAL_HOOKSPATH)], got)
        with self.env():
            os.environ.pop("GIT_CONFIG_SYSTEM", None)
            got = self.writes("git config --system core.hooksPath /x")[0][0]
        self.assertEqual("the system git config" if os.name == "nt" else "/etc/gitconfig", got)

    def test_never_a_repository_config_path(self):
        with self.env():
            for f, _k in self.writes("git config --global core.hooksPath /x"):
                self.assertNotIn(os.sep + ".git" + os.sep, f)
                self.assertNotIn("..", f)

    def test_gated_outside_a_repository_too(self):
        with self.env():
            got = self.writes("git config --global core.hooksPath /x", cwd=self.outside)
        self.assertEqual(1, len(got), "fail-closed: no repository is not a reason to skip it")

    def test_set_subcommand_and_dash_f_and_repo_level(self):
        with self.env():
            self.assertEqual(GC.GLOBAL_HOOKSPATH,
                             self.writes("git config set --global core.hooksPath /x")[0][1])
            self.assertEqual([("/tmp/f.cfg", GC.CONFIG)],
                             self.writes("git config -f /tmp/f.cfg core.hooksPath /x"))
            self.assertEqual(GC.CONFIG, self.writes("git config core.hooksPath /x")[0][1])

    def test_other_global_keys_are_not_widened(self):
        with self.env():
            self.assertEqual([], self.writes("git config --global user.email a@b"))
            self.assertEqual([], self.writes("git config --global core.sshCommand evil"))

    def test_the_hits_door_carries_the_kind(self):
        with self.env():
            got = GC.hits(self.repo, None, [], "git config --global core.hooksPath /x")
        self.assertEqual([GC.GLOBAL_HOOKSPATH], [k for _f, k in got])


class TheGitControlDocIsDerived(unittest.TestCase):
    def test_every_bound_is_in_the_security_doc(self):
        doc = (ROOT / "docs" / "security.md").read_text(encoding="utf-8")
        self.assertIn("## Git hooks and git config", doc)
        self.assertIn("mokata approve <id>", doc)
        self.assertGreaterEqual(len(GC.HONEST_BOUNDS), 7)
        for b in GC.HONEST_BOUNDS:
            self.assertIn(b, doc, "a bound missing from docs/security.md")

    def test_the_docstring_renders_from_the_tuple(self):
        for b in GC.HONEST_BOUNDS:
            self.assertIn(b, GC.__doc__)


if __name__ == "__main__":
    unittest.main()
