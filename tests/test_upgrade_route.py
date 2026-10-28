"""`install_route.plan_upgrade` — the ONE line that upgrades mokata with the installer that OWNS
the environment, keeping its extras (0.0.25 route; display-only since 0.0.26 L2 `MOK-UPDATE`).

Until 0.0.25 the upgrade was always `<python> -m pip install -U mokata`. On the documented primary
route (`uv tool install mokata`) that interpreter has no pip. Business rules:

  * uv tool → `uv tool upgrade mokata` (keeps the extras it installed); a version pin in the
    receipt is said and the line reinstalls without it;
  * pipx → `pipx upgrade mokata`; pip → `-m pip install --upgrade "mokata[<extras>]"`; uv pip →
    `uv pip install --python <py> --upgrade "mokata[<extras>]"`;
  * a dev checkout (editable install) is never upgraded by an installer — "git pull";
  * no installer at all → a command to paste;
  * `argv` is the same command resolved for `mokata update` to run on the human's yes; Windows
    marks it `deferred` (it runs from the detached updater after mokata exits).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import install_route as R

ON_PATH = {"uv": "/usr/bin/uv", "pipx": "/usr/bin/pipx"}.get
NO_EXTRAS = ["mcp>=1.2,<2"]                 # Requires-Dist with no extra installed


def _uv_env(d, requirements='[{ name = "mokata" }]'):
    with open(os.path.join(d, "uv-receipt.toml"), "w", encoding="utf-8") as fh:
        fh.write(f"[tool]\nrequirements = {requirements}\n")
    return d


class EachInstallerUpgradesItsOwnEnvironment(unittest.TestCase):

    def plan(self, d, **kw):
        kw.setdefault("which", ON_PATH)
        kw.setdefault("platform", "linux")
        kw.setdefault("editable", None)
        kw.setdefault("requires", NO_EXTRAS)
        return R.plan_upgrade(prefix=d, executable="/env/bin/python", **kw)

    def test_uv_tool_runs_uv_tool_upgrade(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_uv_env(d))
        self.assertEqual(p.display, "uv tool upgrade mokata")
        self.assertEqual(p.argv, ["/usr/bin/uv", "tool", "upgrade", "mokata"])
        self.assertFalse(p.deferred)

    def test_a_pinned_uv_tool_install_is_said_and_reinstalled_without_the_pin(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_uv_env(d, '[{ name = "mokata", specifier = "==0.0.24" }]'))
        self.assertIn("==0.0.24", p.reason)
        self.assertNotIn("==0.0.24", p.display)
        self.assertTrue(p.display.endswith("--upgrade"), p.display)

    def test_windows_is_deferred_until_mokata_exits(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_uv_env(d), platform="win32")
        self.assertEqual(p.display, "uv tool upgrade mokata")
        self.assertTrue(p.deferred)
        self.assertIn("Windows locks mokata.exe", p.reason)

    def test_an_installer_not_on_path_is_shown_not_run(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_uv_env(d), which=lambda _n: None)
        self.assertIsNone(p.argv)
        self.assertIn("not on PATH", p.reason)

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
        self.assertIsNone(p.argv)
        self.assertIn("uv tool install mokata", p.display)

    def test_a_source_checkout_is_never_upgraded_by_an_installer(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.plan(_uv_env(d), editable="/home/me/mokata")
        self.assertEqual(p.display, "git pull")
        self.assertIsNone(p.argv)
        self.assertIn("/home/me/mokata", p.reason)


if __name__ == "__main__":
    unittest.main()
