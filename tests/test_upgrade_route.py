"""0.0.25 — `mokata upgrade` upgrades with the installer that OWNS the environment.

Until 0.0.25 it always ran `<python> -m pip install -U mokata`. On the documented primary route
(`uv tool install mokata`) that interpreter has no pip, so the upgrade failed — the same defect
Lane I fixed for extras (`INSTALL-EXTRA-NO-PIP-IN-UV-TOOL`). Business rules:

  * uv tool → `uv tool upgrade mokata` (keeps the extras it installed); a version pin in the
    receipt is said, not silently kept; Windows is deferred (the running mokata.exe is locked);
  * pipx → `pipx upgrade mokata`; pip → `-m pip install --upgrade mokata`; uv pip → `uv pip
    install --python <py> --upgrade mokata`;
  * a dev checkout (editable install) is never upgraded for the user — "git pull";
  * no installer at all → a command to paste, never a guess run.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import install_route as R

ON_PATH = {"uv": "/usr/bin/uv", "pipx": "/usr/bin/pipx"}.get


def _uv_env(d, requirements='[{ name = "mokata" }]'):
    with open(os.path.join(d, "uv-receipt.toml"), "w", encoding="utf-8") as fh:
        fh.write(f"[tool]\nrequirements = {requirements}\n")
    return d


class EachInstallerUpgradesItsOwnEnvironment(unittest.TestCase):

    def plan(self, d, **kw):
        kw.setdefault("which", ON_PATH)
        kw.setdefault("platform", "linux")
        kw.setdefault("editable", None)
        return R.plan_upgrade(prefix=d, executable="/env/bin/python", **kw)

    def test_uv_tool_runs_uv_tool_upgrade(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_uv_env(d))
        self.assertEqual(p.argv, ["/usr/bin/uv", "tool", "upgrade", "mokata"])
        self.assertTrue(p.runnable)

    def test_a_pinned_uv_tool_install_is_said_not_silently_kept(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_uv_env(d, '[{ name = "mokata", specifier = "==0.0.24" }]'))
        self.assertFalse(p.runnable)
        self.assertIn("==0.0.24", p.reason)

    def test_windows_uv_tool_is_deferred(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_uv_env(d), platform="win32")
        self.assertTrue(p.deferred)
        self.assertFalse(p.runnable)
        self.assertEqual(p.display, "uv tool upgrade mokata")

    def test_pipx_pip_and_uv_pip(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "pipx_metadata.json"), "w", encoding="utf-8") as fh:
                fh.write("{}")
            self.assertEqual(self.plan(d).argv, ["/usr/bin/pipx", "upgrade", "mokata"])
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.plan(d, has_pip=True).argv,
                             ["/env/bin/python", "-m", "pip", "install", "--upgrade", "mokata"])
            self.assertEqual(self.plan(d, has_pip=False).argv,
                             ["/usr/bin/uv", "pip", "install", "--python", "/env/bin/python",
                              "--upgrade", "mokata"])

    def test_nothing_to_run_with_shows_a_command(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(d, has_pip=False, which=lambda _n: None)
        self.assertFalse(p.runnable)
        self.assertIn("uv tool install mokata", p.display)

    def test_a_source_checkout_is_never_upgraded_for_you(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_uv_env(d), editable="/home/me/mokata")
        self.assertFalse(p.runnable)
        self.assertEqual(p.display, "git pull")
        self.assertIn("/home/me/mokata", p.reason)


def run_cli(argv):
    import io
    from contextlib import redirect_stdout
    from mokata.cli import main
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class TheCommandUsesThePlan(unittest.TestCase):
    """`mokata upgrade` (auto) runs the owning installer's command — not pip."""

    def test_auto_on_a_uv_tool_env_runs_uv_tool_upgrade(self):
        from unittest import mock
        from mokata.version import UpgradeRun
        real = R.plan_upgrade
        ran = []
        with tempfile.TemporaryDirectory() as env, tempfile.TemporaryDirectory() as repo:
            _uv_env(env)
            with mock.patch.object(R, "plan_upgrade",
                                   lambda **kw: real(prefix=env, which=ON_PATH, platform="linux",
                                                     editable=None)), \
                    mock.patch("mokata.version.detect_install_method", return_value="pip"), \
                    mock.patch("mokata.version.run_upgrade",
                               side_effect=lambda plan, *a, **k: ran.append(plan.argv)
                               or UpgradeRun(plan.argv, 0)), \
                    mock.patch("mokata.version._default_tail_runner", return_value=0):
                rc, out = run_cli(["upgrade", "--path", repo, "--yes", "--no-refresh"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(ran, [["/usr/bin/uv", "tool", "upgrade", "mokata"]])


if __name__ == "__main__":
    unittest.main()
