"""Stage 66 — cross-platform parity (Windows / macOS / Linux).

Proves the OS-specific hot paths behave os-agnostically: the CI matrix runs the suite on all
three OSes; the machine-path-free bundle strips Windows absolute paths too (not just POSIX);
the cross-platform user/basename helpers don't assume POSIX; and the Stage-53b `mokata-hook`
console entry needs no `sh`/`launch.sh`. Each Windows rough edge fixed here has a regression
test. Real Windows execution is covered by the CI matrix (the MANUAL-VERIFICATION leg, like
live-db); these tests assert the behaviour os-agnostically so they run on any host.
"""

import os
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import crossplat
from mokata.session_bundle import (
    _machine_path_free,
    find_abs_paths,
)


CI_YML = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "ci.yml")


class TestCIMatrixCoversAllOSes(unittest.TestCase):
    """ci.yml runs the unit suite on windows + macos + ubuntu (× the Python/jsonschema axes)."""

    def _ci(self):
        with open(CI_YML, encoding="utf-8") as fh:
            return fh.read()

    def test_matrix_lists_all_three_oses(self):
        text = self._ci()
        try:
            import yaml
        except ImportError:
            # core stays dependency-free — fall back to a structural text assertion
            for os_name in ("ubuntu-latest", "windows-latest", "macos-latest"):
                self.assertIn(os_name, text, f"{os_name} missing from ci.yml")
            return
        doc = yaml.safe_load(text)
        matrix = doc["jobs"]["test"]["strategy"]["matrix"]
        self.assertIn("os", matrix, "the test matrix has no `os` axis")
        self.assertEqual(set(matrix["os"]),
                         {"ubuntu-latest", "windows-latest", "macos-latest"})
        # still spans the existing axes (no regression of coverage)
        self.assertEqual(set(matrix["jsonschema"]), {"absent", "present"})
        self.assertIn("3.12", [str(v) for v in matrix["python"]])

    def test_test_job_runs_on_the_matrix_os(self):
        text = self._ci()
        try:
            import yaml
        except ImportError:
            self.assertIn("runs-on: ${{ matrix.os }}", text)
            return
        doc = yaml.safe_load(text)
        self.assertEqual(doc["jobs"]["test"]["runs-on"], "${{ matrix.os }}")
        # the live-db job stays Linux-only (services are Linux containers)
        self.assertEqual(doc["jobs"]["live-db"]["runs-on"], "ubuntu-latest")

    def test_shell_is_pinned_for_windows_compat(self):
        """The bash steps (mktemp, pip uninstall || true) must run under bash on every OS —
        windows-latest defaults to pwsh, which would break them."""
        text = self._ci()
        self.assertIn("shell: bash", text)


class TestBasenameAnySeparatorAgnostic(unittest.TestCase):
    def test_posix_basename(self):
        self.assertEqual(crossplat.basename_any("/home/jas/proj/file.py"), "file.py")

    def test_windows_basename_on_any_host(self):
        # the bug: os.path.basename on POSIX never splits on '\\'
        self.assertEqual(crossplat.basename_any(r"C:\Users\jas\proj"), "proj")
        self.assertEqual(crossplat.basename_any(r"C:\Users\jas\file.py"), "file.py")

    def test_unc_path(self):
        self.assertEqual(crossplat.basename_any(r"\\host\share\thing"), "thing")

    def test_mixed_and_trailing_separators(self):
        self.assertEqual(crossplat.basename_any("C:/Users/jas/proj/"), "proj")
        self.assertEqual(crossplat.basename_any("/a/b/"), "b")


class TestCurrentUserCrossPlatform(unittest.TestCase):
    def test_returns_a_string_and_never_crashes(self):
        self.assertIsInstance(crossplat.current_user(), str)

    def test_honours_windows_username_var(self):
        """On Windows the user lives in %USERNAME%, not $USER. getpass-based resolution must
        pick it up even when USER/LOGNAME are absent (the POSIX-only assumption we fixed)."""
        saved = {k: os.environ.get(k) for k in ("USER", "LOGNAME", "LNAME", "USERNAME")}
        try:
            for k in ("USER", "LOGNAME", "LNAME"):
                os.environ.pop(k, None)
            os.environ["USERNAME"] = "winuser"
            self.assertEqual(crossplat.current_user(), "winuser")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class TestMachinePathFreeBundleStripsWindowsPaths(unittest.TestCase):
    """The machine-path-free invariant must hold for Windows absolute paths too — even when
    the scrub runs on a POSIX host (CI, most contributors)."""

    def test_windows_abs_paths_collapse_to_basename(self):
        state = {
            "source": r"C:\Users\jas\proj",
            "dest": r"\\host\share\x",
            "root": "C:/Users/jas/other",
            "unix": "/home/jas/p",
            "note": "touch C:\\Users\\jas\\proj then build",   # mid-prose: NOT a path value
        }
        scrubbed = _machine_path_free(state)
        self.assertEqual(scrubbed["source"], "proj")
        self.assertEqual(scrubbed["dest"], "x")
        self.assertEqual(scrubbed["root"], "other")
        self.assertEqual(scrubbed["unix"], "p")
        # mid-prose text is untouched (only wholly-absolute VALUES are neutralised)
        self.assertIn("C:\\Users", scrubbed["note"])

    def test_no_absolute_paths_survive(self):
        state = {"a": r"C:\x\y", "b": r"\\srv\s\f", "c": "/abs/p", "d": "C:/m/n"}
        self.assertEqual(find_abs_paths(_machine_path_free(state)), [])


class TestHookCommandNeedsNoShell(unittest.TestCase):
    """Stage-53b win holds on Windows: hooks + statusline wire the `mokata-hook` console entry,
    never `sh launch.sh` / a bare `python3`."""

    def test_hook_command_uses_console_entry_not_sh(self):
        from mokata.harness_setup import _hook_command
        cmd = _hook_command("session_start.py")
        self.assertIn("mokata-hook", cmd)
        self.assertIn("session-start", cmd)
        self.assertNotIn("launch.sh", cmd)
        self.assertNotIn("sh ", cmd)
        self.assertNotRegex(cmd, r"\bpython3?\b")

    def test_secret_guard_hook_command_uses_console_entry(self):
        from mokata.harness_setup import _hook_command
        cmd = _hook_command("secret_guard.py")
        self.assertIn("mokata-hook", cmd)
        self.assertIn("secret-guard", cmd)
        self.assertNotIn("launch.sh", cmd)

    def test_statusline_command_uses_console_entry_not_sh(self):
        from mokata.harness_setup import _statusline_command
        cmd = _statusline_command()
        self.assertIn("mokata-hook", cmd)
        self.assertIn("statusline", cmd)
        self.assertNotIn("launch.sh", cmd)
        self.assertNotRegex(cmd, r"\bpython3?\b")


class TestPathsAreSeparatorAgnostic(unittest.TestCase):
    """State / temp_local / bundle paths are built with os.path.join (never a hardcoded '/'),
    so they're correct on Windows."""

    def test_bundle_dir_uses_os_join(self):
        from mokata.session_bundle import bundle_dir
        root = os.path.join("some", "repo")
        d = bundle_dir(root)
        # built with os.path.join -> platform separator, normpath-stable, under .mokata
        self.assertEqual(os.path.normpath(d), d)
        self.assertIn(".mokata" + os.sep, d + os.sep)
        self.assertTrue(d.startswith(root + os.sep))

    def test_bundle_path_under_mokata_dir(self):
        import tempfile

        from mokata.session_bundle import bundle_path
        with tempfile.TemporaryDirectory() as root:
            p = bundle_path(root, "auth-refactor")
            self.assertEqual(os.path.normpath(p), p)
            self.assertTrue(p.endswith(os.path.join("session-bundles", "auth-refactor.json")))


if __name__ == "__main__":
    unittest.main()
