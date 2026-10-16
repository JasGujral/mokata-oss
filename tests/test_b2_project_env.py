"""0.0.23 B2 — `VENV-TEST-INTERPRETER`: the project's tests run in the PROJECT's env, activated or not.

`baseline.baseline_status` used to inherit mokata's own process environment. From the MCP server,
a hook or the IDE that environment is whatever Claude Code was launched with — so `pytest` was not
found, or ran without the project's dependencies, and the baseline reported a false RED.

These tests plant a FAKE test runner inside an env's `bin/` (`Scripts\\` on Windows) and run the
baseline from a parent environment with NO venv activated: only explicit resolution can find it.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import project_env
from mokata.baseline import GREEN, RED, baseline_status

_RUNNER = "mokfaketests"   # a name that exists on no machine's PATH


def _clean_environ():
    """The parent env of an MCP server started outside any venv."""
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "CONDA_PREFIX")}
    return env


def _bin(env_dir):
    return os.path.join(env_dir, "Scripts" if os.name == "nt" else "bin")


def _plant_runner(env_dir, exit_code=0):
    os.makedirs(_bin(env_dir), exist_ok=True)
    if os.name == "nt":
        path = os.path.join(_bin(env_dir), _RUNNER + ".bat")
        body = "@echo off\r\necho ran-in-%VIRTUAL_ENV%\r\nexit /b {}\r\n".format(exit_code)
    else:
        path = os.path.join(_bin(env_dir), _RUNNER)
        body = "#!/bin/sh\necho \"ran-in-$VIRTUAL_ENV\"\nexit {}\n".format(exit_code)
    with open(path, "wb") as fh:
        fh.write(body.encode("utf-8"))
    os.chmod(path, 0o755)


def _make_env(parent, name, exit_code=0):
    # normpath: `name` may be a POSIX-style relative path ("tools/py"); joined on Windows that is
    # `...\\tools/py`, while mokata reports the platform's own spelling `...\\tools\\py`
    # (0.0.23 cut, Windows CI legs). The expectation must be spelled the same way.
    d = os.path.normpath(os.path.join(parent, name))
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "pyvenv.cfg"), "wb") as fh:
        fh.write(b"home = /usr/bin\n")
    _plant_runner(d, exit_code)
    return d


class TheLiveDefect(unittest.TestCase):
    """A repo whose test runner exists only inside its own `venv/`, run with nothing activated."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_the_repo_env_runs_the_tests_with_no_venv_activated(self):
        env_dir = _make_env(self.root, "venv")
        r = baseline_status(_RUNNER, cwd=self.root, environ=_clean_environ())
        self.assertEqual(r.state, GREEN, r.detail)
        self.assertIn("ran-in-" + env_dir, r.detail)
        self.assertIn("venv/ in the repo", r.render())

    def test_a_failing_suite_in_the_repo_env_is_RED_for_the_right_reason(self):
        _make_env(self.root, ".venv", exit_code=3)
        r = baseline_status(_RUNNER, cwd=self.root, environ=_clean_environ())
        self.assertEqual((r.state, r.returncode), (RED, 3))


class TheResolutionOrder(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_dot_venv_is_preferred_over_other_names(self):
        _make_env(self.root, "aaa_env")
        dot = _make_env(self.root, ".venv")
        found = project_env.resolve(self.root, environ=_clean_environ(), ask_tools=False)
        self.assertEqual(found.path, dot)

    def test_any_named_env_is_found_by_marker(self):
        d = _make_env(self.root, "py312")
        found = project_env.resolve(self.root, environ=_clean_environ(), ask_tools=False)
        self.assertEqual(found.path, d)

    def test_an_activated_VIRTUAL_ENV_is_honoured(self):
        _make_env(self.root, "venv")
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        active = _make_env(outside, "active")
        environ = dict(_clean_environ(), VIRTUAL_ENV=active)
        found = project_env.resolve(self.root, environ=environ, ask_tools=False)
        self.assertEqual((found.path, found.how), (active, "$VIRTUAL_ENV"))

    def test_settings_python_overrides_everything(self):
        _make_env(self.root, "venv")
        chosen = _make_env(self.root, "tools/py")
        environ = dict(_clean_environ(), VIRTUAL_ENV=self.root)
        found = project_env.resolve(self.root, "tools/py", environ, ask_tools=False)
        self.assertEqual((found.path, found.how), (chosen, "settings.python"))

    def test_settings_python_may_name_the_interpreter(self):
        chosen = _make_env(self.root, "e")
        exe = os.path.join(_bin(chosen), "python")
        with open(exe, "wb") as fh:
            fh.write(b"")
        found = project_env.resolve(self.root, exe, _clean_environ(), ask_tools=False)
        self.assertEqual(found.path, chosen)

    def test_a_conda_env_activates_CONDA_PREFIX(self):
        d = os.path.join(self.root, "cenv")
        os.makedirs(os.path.join(d, "conda-meta"))
        found = project_env.resolve(self.root, environ=_clean_environ(), ask_tools=False)
        self.assertTrue(found.conda)
        out = project_env.activated(_clean_environ(), found)
        self.assertEqual(out["CONDA_PREFIX"], d)


class NoEnvIsStatedNotGuessed(unittest.TestCase):
    """The healthy no-env path: the command runs exactly as before, and the report SAYS so."""

    def test_no_env_runs_unchanged_and_says_system_python(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        environ = _clean_environ()
        self.assertIsNone(project_env.resolve(root, environ=environ, ask_tools=False))
        self.assertEqual(project_env.activated(environ, None), environ)
        r = baseline_status("exit 0", cwd=root, environ=environ)
        self.assertEqual(r.state, GREEN)
        self.assertIn(project_env.SYSTEM_LINE, r.render())

    def test_activation_puts_the_env_first_on_PATH_and_drops_PYTHONHOME(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        d = _make_env(root, "venv")
        environ = dict(_clean_environ(), PYTHONHOME="/somewhere")
        out = project_env.activated(environ, project_env.resolve(root, environ=environ,
                                                                 ask_tools=False))
        path_key = next(k for k in out if k.upper() == "PATH")
        self.assertEqual(out[path_key].split(os.pathsep)[0], _bin(d))
        self.assertEqual(out["VIRTUAL_ENV"], d)
        self.assertNotIn("PYTHONHOME", out)


class DoctorNamesIt(unittest.TestCase):

    def _surface(self, root):
        class _M:
            def setting(self, key, default=None):
                return default
        class _S:
            pass
        s = _S()
        s.root, s.manifest = root, _M()
        return s

    def test_doctor_names_the_project_python_on_a_python_repo(self):
        from mokata.govern.doctor import project_python_findings
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "pyproject.toml"), "wb") as fh:
            fh.write(b"[project]\nname='x'\n")
        d = _make_env(root, ".venv")
        found = project_python_findings(self._surface(root), environ=_clean_environ())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "info")
        self.assertIn(d, found[0].detail)

    def test_doctor_says_nothing_on_a_non_python_repo(self):
        from mokata.govern.doctor import project_python_findings
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(project_python_findings(self._surface(root), environ=_clean_environ()), [])


if __name__ == "__main__":
    unittest.main()
