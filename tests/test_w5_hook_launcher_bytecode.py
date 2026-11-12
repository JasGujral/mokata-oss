"""0.0.28 W5 (verifier M1) — the plugin's hook launcher runs the hook's Python WITH bytecode, kept in
mokata's own per-user cache and never beside the sources.

Claude Code exports `PYTHONDONTWRITEBYTECODE=1` to every child it spawns, so a hook inherits it. The
pinned runtime is a uv environment, and uv installs without `.pyc`; the source-tree rung (`python3 -m
mokata_emit` on the checkout's `src/`) has none either. Honouring the variable, every hook recompiled
the ~60 modules it imports: 63 ms of the prompt hook's 190, on every prompt, for every user.

The launcher (`mokata-hook-launch`, and `.cmd` for anyone running it from cmd.exe) therefore drops the
inherited variable for the hook's process and points `PYTHONPYCACHEPREFIX` at mokata's user cache —
the ONE rule mokata already has for one (`branch_protection.default_cache_dir`: `$XDG_CACHE_HOME`,
else `~/.cache`, then `mokata/`), here `mokata/pycache`. The plugin checkout is never written into: a
marketplace checkout is not mokata's to dirty, and a prefix is where Python itself puts bytecode when
told to. An unwritable cache degrades to the old cost — Python skips a bytecode write it cannot make —
and never fails the hook.

Each case runs the launcher of a COPY of the plugin's sources (no `__pycache__` in it), so "nothing
beside the sources" is a fact about one fresh tree, not about whatever this checkout's own test runs
left behind. The timed half of the claim is the budget tests: `test_o12_prompt_hook_budget` and
`test_hb_hook_budget` now run every hook through this launcher with the variable set
(`_support.plugin_route_hook`).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

import mokata
from mokata import branch_protection
from mokata.quiet_spawn import hidden

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
CLEAN = ("secret-guard", "--text", "print(1)", "--path", "ok.py")


def _files(top):
    out = set()
    for d, dirs, names in os.walk(top):
        out.update(os.path.relpath(os.path.join(d, n), top) for n in names)
        out.update(os.path.relpath(os.path.join(d, n), top) + os.sep for n in dirs)
    return out


def _pyc(top):
    return sorted(p for p in _files(top) if p.endswith(".pyc"))


class _CopiedPlugin(unittest.TestCase):
    """A fresh copy of the plugin's `src/` (the launcher lives at `src/mokata/hooks/`)."""

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.t = os.path.realpath(cls._t.name)
        cls.src = os.path.join(cls.t, "plugin", "src")
        for pkg in ("mokata", "mokata_emit"):
            shutil.copytree(os.path.join(SRC, pkg), os.path.join(cls.src, pkg),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        cls.launcher = os.path.join(cls.src, "mokata", "hooks", "mokata-hook-launch")
        os.chmod(cls.launcher, os.stat(cls.launcher).st_mode | stat.S_IXUSR)
        cls.before = _files(cls.src)
        assert not _pyc(cls.src), "the copy must start with no bytecode"

    @classmethod
    def tearDownClass(cls):
        cls._t.cleanup()

    def tearDown(self):
        self.assertEqual(set(), _files(self.src) - self.before,
                         "a hook run wrote beside the plugin's sources")

    def _env(self, case, *, rung, **extra):
        """The hook's environment as Claude Code hands it over: the variable SET. `rung` "pinned"
        is the plugin's recorded uv runtime (`PYTHONPATH` standing in for its site-packages);
        "python" is the source-tree rung (the launcher puts the copy's `src/` on the path)."""
        home = os.path.join(self.t, case)
        os.makedirs(home)
        env = {k: v for k, v in os.environ.items()
               if k not in ("MOKATA_HOOK", "MOKATA_PYTHON", "CLAUDE_PLUGIN_DATA", "PYTHONPATH",
                            "PYTHONPYCACHEPREFIX", "XDG_CACHE_HOME")}
        env.update(PYTHONDONTWRITEBYTECODE="1", HOME=home, USERPROFILE=home)
        if rung == "pinned":
            data = os.path.join(home, "plugin-data")
            os.makedirs(data)
            with open(os.path.join(data, f"runtime-{mokata.__version__}.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write(sys.executable + "\n")
            env.update(CLAUDE_PLUGIN_DATA=data, PYTHONPATH=self.src)
        else:
            env.update(MOKATA_PYTHON=sys.executable)
        env.update(extra)
        return home, env

    def _run(self, argv, env):
        res = subprocess.run(argv, env=env, cwd=self.t, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=120, **hidden())
        self.assertEqual(0, res.returncode, res.stderr)
        self.assertEqual("", res.stderr.strip(), "a clean hook run says nothing")
        return res


@unittest.skipUnless(_support.BASH, _support.NO_BASH)
class TheHookLauncherCachesBytecodeInMokatasUserCache(_CopiedPlugin):
    """`mokata-hook-launch` — the launcher `hooks.json` runs (bash on every platform)."""

    def test_one_run_with_the_variable_set_leaves_bytecode_under_the_prefix_and_none_beside(self):
        for rung in ("pinned", "python"):
            with self.subTest(rung=rung):
                home, env = self._env(rung, rung=rung,
                                      XDG_CACHE_HOME=os.path.join(self.t, rung + "-xdg"))
                self._run(_support.bash_argv(self.launcher, *CLEAN), env)
                prefix = os.path.join(self.t, rung + "-xdg", "mokata", "pycache")
                pyc = _pyc(prefix)
                own = ["mokata_emit", "__init__.cpython-%d%d.pyc" % sys.version_info[:2]]
                self.assertTrue(any(p.split(os.sep)[-2:] == own for p in pyc),
                                f"the hook's own modules are cached under the prefix: {pyc[:5]}")
                self.assertGreater(len(pyc), 5, pyc)

    def test_the_prefix_is_mokatas_one_user_cache_rule(self):
        """No second rule: the launcher's directory is `branch_protection.default_cache_dir`'s
        parent + `pycache`, under `$XDG_CACHE_HOME` and, without it, under the home directory."""
        for case, xdg in (("rule-xdg", True), ("rule-home", False)):
            with self.subTest(case=case):
                home, env = self._env(case, rung="python")
                if xdg:
                    env["XDG_CACHE_HOME"] = os.path.join(home, "xdg")
                self._run(_support.bash_argv(self.launcher, *CLEAN), env)
                with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
                    os.environ.pop("XDG_CACHE_HOME", None)
                    if xdg:
                        os.environ["XDG_CACHE_HOME"] = env["XDG_CACHE_HOME"]
                    rule = os.path.join(os.path.dirname(branch_protection.default_cache_dir()),
                                        "pycache")
                self.assertTrue(_pyc(rule), f"bytecode under the user-cache rule's {rule}")
                self.assertEqual({rule}, {os.path.join(home, d, "mokata", "pycache")
                                          for d in os.listdir(home)
                                          if os.path.isdir(os.path.join(home, d, "mokata",
                                                                        "pycache"))},
                                 "and nowhere else under the user's home")

    def test_an_unwritable_cache_degrades_never_fails(self):
        home, env = self._env("unwritable", rung="python")
        blocker = os.path.join(home, "a-file-not-a-directory")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        env["XDG_CACHE_HOME"] = blocker
        self._run(_support.bash_argv(self.launcher, *CLEAN), env)        # rc 0, silent

    @unittest.skipIf(os.name == "nt", "Git Bash supplies a HOME of its own")
    def test_with_no_cache_to_name_the_variable_is_left_alone(self):
        """Neither `$XDG_CACHE_HOME` nor a home: there is no mokata cache, so the inherited
        variable stays — dropping it would put `__pycache__` beside the sources."""
        _home, env = self._env("no-home", rung="python")
        env.pop("HOME")
        self._run(_support.bash_argv(self.launcher, *CLEAN), env)


@unittest.skipUnless(os.name == "nt", "the .cmd launcher runs on Windows only")
class TheCmdLauncherCachesBytecodeInMokatasUserCache(_CopiedPlugin):
    """`mokata-hook-launch.cmd` — the same contract for anyone who runs it from cmd.exe."""

    def _cmd(self):
        return [os.path.join(self.src, "mokata", "hooks", "mokata-hook-launch.cmd"), *CLEAN]

    def test_one_run_with_the_variable_set_leaves_bytecode_under_the_prefix_and_none_beside(self):
        for rung in ("pinned", "python"):
            with self.subTest(rung=rung):
                _home, env = self._env("cmd-" + rung, rung=rung,
                                       XDG_CACHE_HOME=os.path.join(self.t, "cmd-" + rung + "-xdg"))
                self._run(self._cmd(), env)
                self.assertGreater(len(_pyc(os.path.join(self.t, "cmd-" + rung + "-xdg", "mokata",
                                                         "pycache"))), 5)

    def test_without_xdg_the_prefix_is_under_the_users_profile(self):
        home, env = self._env("cmd-profile", rung="python")
        self._run(self._cmd(), env)
        self.assertGreater(len(_pyc(os.path.join(home, ".cache", "mokata", "pycache"))), 5)

    def test_an_unwritable_cache_degrades_never_fails(self):
        home, env = self._env("cmd-unwritable", rung="python")
        blocker = os.path.join(home, "a-file-not-a-directory")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        env["XDG_CACHE_HOME"] = blocker
        self._run(self._cmd(), env)


if __name__ == "__main__":
    unittest.main()
