"""Stage 45b — version & upgrade UX.

`mokata version` is OFFLINE by default (local-first, zero egress). The update check is
OPT-IN, netguard-accounted, and degrade-clean offline. `mokata upgrade` proposes a
human-gated `pip install -U` (pip install) or prints the plugin-update steps (plugin
install) — it never auto-runs. Both jsonschema states; no real network in tests.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

import mokata
from mokata import __version__
from mokata.cli import main
from mokata.govern import AuditLedger
from mokata.netguard import no_network
from mokata.version import (
    UpdateCheck,
    VersionInfo,
    check_for_update,
    detect_install_method,
    run_pip_upgrade,
    upgrade_steps,
    version_info,
)


def run_cli(argv, stdin=""):
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with redirect_stdout(buf):
            rc = main(argv)
    finally:
        sys.stdin = old
    return rc, buf.getvalue()


def _repo_root():
    # mokata/__init__.py -> .../src/mokata/__init__.py ; root is two dirs up from src/mokata
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(mokata.__file__))))


# --- version display is offline / local-first -----------------------------------
class TestVersionDisplayOffline(unittest.TestCase):
    def test_version_info_carries_the_facts(self):
        info = version_info(profile="standard")
        self.assertIsInstance(info, VersionInfo)
        self.assertEqual(info.version, __version__)
        self.assertEqual(info.profile, "standard")
        self.assertIn(info.install_method, ("pip", "plugin", "source"))
        self.assertTrue(info.python)

    def test_version_info_makes_no_network(self):
        with no_network():                       # proves zero egress on the display path
            info = version_info(profile="standard")
            _ = info.render()
        self.assertIn(__version__, info.render())

    def test_cmd_version_prints_offline(self):
        with tempfile.TemporaryDirectory() as d:
            with no_network():
                rc, out = run_cli(["version", "--path", d])
        self.assertEqual(rc, 0)
        self.assertIn(__version__, out)
        self.assertIn("python", out.lower())


# --- install-method detection ---------------------------------------------------
class TestInstallDetection(unittest.TestCase):
    def test_plugin_when_root_cache_points_at_the_package(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".mokata"))
            with open(os.path.join(home, ".mokata", "plugin-root"), "w") as fh:
                fh.write(_repo_root() + "\n")
            self.assertEqual(detect_install_method(home=home), "plugin")

    def test_pip_when_in_site_packages(self):
        with tempfile.TemporaryDirectory() as home:
            method = detect_install_method(
                home=home,
                package_file="/opt/py/lib/python3.11/site-packages/mokata/version.py")
            self.assertEqual(method, "pip")

    def test_source_when_no_cache_and_not_site_packages(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(detect_install_method(home=home), "source")


# --- update check: opt-in, accounted, degrade-clean -----------------------------
class TestUpdateCheck(unittest.TestCase):
    def test_reports_a_newer_release(self):
        chk = check_for_update("0.0.3", fetcher=lambda _url: "v0.0.9")
        self.assertTrue(chk.ok)
        self.assertEqual(chk.latest, "0.0.9")
        self.assertFalse(chk.up_to_date)

    def test_reports_up_to_date(self):
        chk = check_for_update("0.0.9", fetcher=lambda _url: "0.0.9")
        self.assertTrue(chk.ok)
        self.assertTrue(chk.up_to_date)

    def test_degrades_clean_when_the_fetch_fails(self):
        def boom(_url):
            raise OSError("offline")
        chk = check_for_update("0.0.3", fetcher=boom)
        self.assertFalse(chk.ok)
        self.assertIn("couldn't check", chk.message.lower())

    def test_offline_default_fetch_is_blocked_and_degrades_clean(self):
        # netguard-accounted: the only egress path; blocked offline -> never raises.
        with no_network():
            chk = check_for_update("0.0.3")
        self.assertFalse(chk.ok)

    def test_check_is_accounted_in_the_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            check_for_update("0.0.3", fetcher=lambda _url: "v0.0.3", ledger=led)
            self.assertTrue(any(e["kind"] == "update_check" for e in led.entries()))


# --- upgrade: human-gated, never auto-runs --------------------------------------
class TestUpgrade(unittest.TestCase):
    def test_pip_upgrade_runs_the_right_command_via_runner(self):
        spy = []
        cmd = run_pip_upgrade(runner=lambda c: spy.append(c))
        self.assertEqual(cmd[-3:], ["install", "-U", "mokata"])
        self.assertEqual(spy[0][-3:], ["install", "-U", "mokata"])

    def test_upgrade_steps_per_method(self):
        self.assertIn("pip install -U mokata", " ".join(upgrade_steps("pip")))
        self.assertIn("marketplace update", " ".join(upgrade_steps("plugin")))

    def test_cmd_upgrade_pip_proposes_without_running(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = run_cli(["upgrade", "--method", "pip", "--path", d], stdin="")
        self.assertEqual(rc, 0)
        self.assertIn("pip install -U mokata", out)
        self.assertIn("not run", out.lower())          # declined at the human gate

    def test_cmd_upgrade_plugin_prints_steps(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = run_cli(["upgrade", "--method", "plugin", "--path", d])
        self.assertEqual(rc, 0)
        self.assertIn("/plugin marketplace update mostack", out)


# --- /mokata:version slash command (generated from skills.py) -------------------
class TestVersionSkill(unittest.TestCase):
    def test_version_skill_registered_and_template_in_sync(self):
        from mokata.skills import command_markdown, get_skill
        skill = get_skill("version")
        path = os.path.join(os.path.dirname(__file__), "..", "templates",
                            "commands", "version.md")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), command_markdown(skill))


if __name__ == "__main__":
    unittest.main()
