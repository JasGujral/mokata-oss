"""0.0.23 B4 — INSTALL-ISOLATED-BY-DEFAULT: doctor + setup name a bad install, silent when healthy.

Two shapes the user cannot see without being told:

  * mokata installed INSIDE the project's own environment (`sys.prefix` under the repo root) —
    warns with the isolated install commands and the reason;
  * SHADOWING — PATH's `mokata` is another install at another version than the one running.

Every case is planted (fake prefixes, fake `which`, a planted venv's dist-info METADATA); nothing
depends on what is installed on the machine running the suite. Windows-safe: paths built with
os.path, both `bin/lib/pythonX/site-packages` and `Scripts/Lib/site-packages` layouts covered.
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import install_isolation as II
from mokata.govern import doctor


def _no_path_mokata(_name):
    return None


class InProjectEnv(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_prefix_inside_repo_warns_with_isolated_installs_and_why(self):
        text = II.in_project_env(self.root, os.path.join(self.root, ".venv"))
        self.assertIsNotNone(text)
        for cmd in ("pipx install mokata", "uv tool install mokata",
                    "brew install JasGujral/mokata/mokata"):
            self.assertIn(cmd, text)
        self.assertIn("dependencies", text)          # pollutes project deps
        self.assertIn("recreated", text)             # breaks when the venv is recreated
        self.assertIn("hooks", text)                 # hooks bind to it

    def test_prefix_outside_repo_is_silent(self):
        other = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(other, ignore_errors=True))
        self.assertIsNone(II.in_project_env(self.root, os.path.join(other, "venvs", "mokata")))

    def test_sibling_dir_with_common_name_prefix_is_not_inside(self):
        # `/x/repo-venv` is not inside `/x/repo` — commonpath, not a string prefix.
        self.assertIsNone(II.in_project_env(self.root, self.root + "-venv"))

    def test_mokata_own_checkout_is_the_contributor_setup_not_a_warning(self):
        with open(os.path.join(self.root, "pyproject.toml"), "w", encoding="utf-8") as fh:
            fh.write('[project]\nname = "mokata"\nversion = "0.0.23"\n')
        self.assertIsNone(II.in_project_env(self.root, os.path.join(self.root, ".venv")))

    def test_other_project_named_differently_still_warns(self):
        with open(os.path.join(self.root, "pyproject.toml"), "w", encoding="utf-8") as fh:
            fh.write('[project]\nname = "my-app"\n')
        self.assertIsNotNone(II.in_project_env(self.root, os.path.join(self.root, "venv")))


class Shadowing(unittest.TestCase):
    def test_older_mokata_ahead_on_path_warns(self):
        fake = "/somewhere/proj/.venv/bin/mokata"   # an opaque string the warning echoes back
        text = II.shadowed(which=lambda n: fake, own_scripts=os.path.join("tools", "bin"),
                           own_version="0.0.23", version_of=lambda exe: "0.0.20")
        self.assertIsNotNone(text)
        self.assertIn("0.0.20", text)
        self.assertIn("0.0.23", text)
        self.assertIn("older", text)
        self.assertIn(fake, text)

    def test_same_version_elsewhere_is_silent(self):
        self.assertIsNone(II.shadowed(which=lambda n: "/x/bin/mokata", own_scripts="/y/bin",
                                      own_version="0.0.23", version_of=lambda e: "0.0.23"))

    def test_no_mokata_on_path_is_silent(self):
        self.assertIsNone(II.shadowed(which=_no_path_mokata, own_scripts="/y/bin",
                                      own_version="0.0.23", version_of=lambda e: "0.0.1"))

    def test_unknowable_version_is_silent_not_a_guess(self):
        self.assertIsNone(II.shadowed(which=lambda n: "/x/bin/mokata", own_scripts="/y/bin",
                                      own_version="0.0.23", version_of=lambda e: None))

    def test_path_resolving_to_this_install_never_probes(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))

        def boom(_exe):
            raise AssertionError("must not probe its own install")
        self.assertIsNone(II.shadowed(which=lambda n: os.path.join(d, "mokata"), own_scripts=d,
                                      own_version="0.0.23", version_of=boom))


class ReadsTheSiblingCheaply(unittest.TestCase):
    def _plant(self, layout, version):
        prefix = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(prefix, ignore_errors=True))
        with open(os.path.join(prefix, "pyvenv.cfg"), "w", encoding="utf-8") as fh:
            fh.write("home = /usr/bin\n")
        if layout == "posix":
            bindir = os.path.join(prefix, "bin")
            site = os.path.join(prefix, "lib", "python3.12", "site-packages")
        else:
            bindir = os.path.join(prefix, "Scripts")
            site = os.path.join(prefix, "Lib", "site-packages")
        os.makedirs(bindir)
        info = os.path.join(site, "mokata-%s.dist-info" % version)
        os.makedirs(info)
        with open(os.path.join(info, "METADATA"), "w", encoding="utf-8") as fh:
            fh.write("Metadata-Version: 2.1\nName: mokata\nVersion: %s\n\nbody\n" % version)
        exe = os.path.join(bindir, "mokata")
        with open(exe, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/false\n")
        return exe

    def test_posix_venv_metadata_read_without_running(self):
        exe = self._plant("posix", "0.0.19")
        with mock.patch.object(II, "_ask_version", side_effect=AssertionError("ran it")):
            self.assertEqual(II.path_mokata_version(exe), "0.0.19")

    def test_windows_venv_layout_metadata_read_without_running(self):
        exe = self._plant("windows", "0.0.18")
        with mock.patch.object(II, "_ask_version", side_effect=AssertionError("ran it")):
            self.assertEqual(II.path_mokata_version(exe), "0.0.18")

    def test_non_venv_falls_back_to_bounded_version_call(self):
        seen = {}

        def fake_run(argv, **kw):
            seen.update(kw, argv=argv)
            return SimpleNamespace(stdout="mokata 0.0.21\n", stderr="")
        with mock.patch.object(II.subprocess, "run", side_effect=fake_run):
            self.assertEqual(II._ask_version("/opt/x/mokata"), "0.0.21")
        self.assertEqual(seen["timeout"], 3)
        self.assertEqual(seen["argv"], ["/opt/x/mokata", "--version"])

    def test_version_call_timeout_is_silence(self):
        with mock.patch.object(II.subprocess, "run",
                               side_effect=II.subprocess.TimeoutExpired("m", 3)):
            self.assertIsNone(II._ask_version("/opt/x/mokata"))

    def test_real_bounded_call_parses_a_version(self):
        # A real subprocess, cross-platform: the interpreter answers `--version` too.
        self.assertRegex(II._ask_version(sys.executable) or "", r"^\d+\.\d+")


class DoctorAndSetupSurfaceIt(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_doctor_warning_when_in_project(self):
        found = doctor.install_isolation_findings(
            SimpleNamespace(root=self.root), prefix=os.path.join(self.root, ".venv"),
            which=_no_path_mokata)
        self.assertEqual([(f.severity, f.code) for f in found],
                         [("warning", II.CODE_IN_PROJECT)])

    def test_doctor_warning_when_shadowed(self):
        found = doctor.install_isolation_findings(
            SimpleNamespace(root=self.root), prefix=os.path.join(os.sep, "elsewhere"),
            which=lambda n: "/v/bin/mokata", own_scripts="/t/bin", own_version="0.0.23",
            version_of=lambda e: "0.0.20")
        self.assertEqual([f.code for f in found], [II.CODE_SHADOWED])

    def test_doctor_silent_when_healthy(self):
        self.assertEqual(doctor.install_isolation_findings(
            SimpleNamespace(root=self.root), prefix=os.path.join(os.sep, "elsewhere"),
            which=_no_path_mokata), [])

    def test_diagnose_includes_the_check(self):
        import inspect
        self.assertIn("install_isolation_findings(surface)", inspect.getsource(doctor.diagnose))

    def test_a_raising_check_never_takes_doctor_down(self):
        def boom(_n):
            raise RuntimeError("x")
        self.assertEqual(II.warnings(self.root, prefix=os.path.join(os.sep, "elsewhere"),
                                     which=boom), [])

    def test_setup_prints_the_warning_before_the_confirm(self):
        from mokata import harness_setup
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        lines = []
        asked = []
        with mock.patch.object(II, "warnings",
                               return_value=[(II.CODE_IN_PROJECT, "PLANTED-INSTALL-WARNING")]):
            res = harness_setup.setup_harness(
                "claude", root=self.root, home=home, out=lines.append,
                confirm=lambda prompt: asked.append(len(lines)) or False)
        self.assertTrue(res.aborted)                 # declined: nothing written
        joined = "\n".join(lines)
        self.assertIn("PLANTED-INSTALL-WARNING", joined)
        self.assertIn(II.CODE_IN_PROJECT, joined)
        # it was on screen before the user was asked
        self.assertTrue(any("PLANTED-INSTALL-WARNING" in l for l in lines[:asked[0]]))

    def test_setup_silent_when_healthy(self):
        from mokata import harness_setup
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(home, ignore_errors=True))
        lines = []
        with mock.patch.object(II, "warnings", return_value=[]):
            harness_setup.setup_harness("claude", root=self.root, home=home, out=lines.append,
                                        confirm=lambda p: False)
        joined = "\n".join(lines)
        self.assertNotIn(II.CODE_IN_PROJECT, joined)
        self.assertNotIn(II.CODE_SHADOWED, joined)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- the docs lead with isolation
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_INSTALL_DOCS = ("README.md", "docs/getting-started.md", "docs/quickstart.md", "docs/index.md")
_CAVEAT = "inside a project venv this mixes mokata's deps into yours"


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class InstallDocsLeadWithTheOneInstruction(unittest.TestCase):
    """INSTALL-ISOLATED-BY-DEFAULT, docs half — re-ordered at the 0.0.23 cut by the owner: every
    page that tells a user how to install LEADS with the one instruction that both sets mokata up
    and updates it (`uv tool install --upgrade mokata`, then `mokata setup claude`), then the
    plugin as the alternative, and plain pip last with the caveat. (README-ONE-JOB: the README's
    install block sits within the first screen.)"""

    ONE = "uv tool install --upgrade mokata"

    def test_order_one_instruction_then_plugin_then_pip(self):
        for rel in _INSTALL_DOCS:
            text = _read(rel)
            one = text.find(self.ONE)
            setup = text.find("mokata setup claude", max(one, 0))
            plugin = text.find("/plugin marketplace add JasGujral/mokata-oss")
            pip = text.find("pip install mokata")
            for name, at in (("the one instruction", one), ("setup claude", setup),
                             ("the plugin", plugin), ("plain pip", pip)):
                self.assertNotEqual(at, -1, "%s: %s is missing" % (rel, name))
            self.assertLess(one, plugin, "%s: the one instruction must come before the plugin" % rel)
            self.assertLess(setup, plugin, "%s: `mokata setup claude` must come before the plugin"
                            % rel)
            self.assertLess(plugin, pip, "%s: the plugin must come before plain pip" % rel)
            self.assertIn("pipx install --force mokata", text, "%s: the pipx form is named" % rel)
            self.assertIn("/plugin install mokata@mostack", text, rel)
            # wrapped prose / blockquotes: drop the `> ` markers and collapse whitespace
            flat = " ".join(" ".join(l.lstrip("> ") for l in text.splitlines()).split())
            self.assertIn(_CAVEAT, flat, rel)
            self.assertIn("mokata init", flat, "%s: says setup runs init for you" % rel)

    def test_marketplace_handle_matches_the_manifest(self):
        import json
        with open(os.path.join(_ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as fh:
            name = json.load(fh)["name"]
        for rel in _INSTALL_DOCS:
            self.assertIn("/plugin install mokata@%s" % name, _read(rel), rel)

    def test_readme_install_is_on_the_first_screen(self):
        lines = _read("README.md").splitlines()
        idx = next(i for i, l in enumerate(lines) if l.strip() == "## Install")
        self.assertLess(idx, 30, "the install block must be within the README's first screen")

    def test_no_install_page_still_calls_the_plugin_unavailable(self):
        for rel in _INSTALL_DOCS + ("docs/how-to/install-plugin.md", "docs/how-to/use-the-plugin.md",
                                    "docs/how-to/use-without-plugin.md",
                                    "docs/how-to/which-setup-command.md",
                                    "docs/concepts/execution-model.md",
                                    "docs/how-to/integrate-other-ai-tools.md",
                                    "docs/tutorials/mokata-complete-guide.md"):
            # line-scoped to the plugin: "a VS Code extension is planned, not yet available" is true
            low = "\n".join(l for l in _read(rel).lower().splitlines()
                             if "plugin" in l and "vs code" not in l)
            for stale in ("planned, not yet available", "planned but not yet available",
                          "planned but **not yet available**", "not yet registered on",
                          "isn't registered on any", "plugin-directory approval"):
                self.assertNotIn(stale, low, "%s still says %r" % (rel, stale))


class RequiresPython(unittest.TestCase):
    def test_pyproject_refuses_old_pythons(self):
        # VENV-INSTALL-ERROR candidate cause: a <3.10 venv. pip refuses it via requires-python.
        self.assertRegex(_read("pyproject.toml"), r'(?m)^requires-python\s*=\s*">=3\.10"')
