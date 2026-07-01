"""Stage 23 — init from inside Claude Code (no terminal "init every time").

Covers (both jsonschema states):
  - `mokata init --preview` prints the plan and writes nothing.
  - the SessionStart plugin-root cache (record/read), isolated from the real ~.
  - project-root resolution: an initialized repo is recognized across a reload AND from a
    subdirectory (the "asks every time" root bug).
  - the SessionStart offer fires on a fresh repo and NOT on an initialized one, even when the
    hook re-runs (new-session simulation).
  - the MCP `init` tool is propose-only without confirm; commits with confirm; force to overwrite.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR
from mokata.cli import main
from mokata.config import Surface, find_project_root
from mokata.init import init_repo
from mokata.plugin_cache import read_plugin_root, record_plugin_root
from mokata.profiles import profile_names

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "session_start.py")


def _run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


def _silent(_):
    pass


# --------------------------------------------------------------- 1. init --preview

class TestInitPreview(unittest.TestCase):
    def test_preview_writes_nothing_every_profile(self):
        for profile in profile_names():
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as d:
                rc, out = _run_cli(["init", "--profile", profile, "--preview",
                                    "--path", d])
                self.assertEqual(rc, 0)
                self.assertIn(profile, out)
                self.assertFalse(os.path.exists(os.path.join(d, MOKATA_DIR)),
                                 f"{profile}: --preview created .mokata/")

    def test_preview_is_noop_when_manifest_exists(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            manifest = os.path.join(d, MOKATA_DIR, "manifest.json")
            with open(manifest, encoding="utf-8") as fh:
                before = fh.read()
            rc, _ = _run_cli(["init", "--profile", "full", "--preview", "--path", d])
            self.assertEqual(rc, 0)
            # the manifest is untouched — preview never writes
            with open(manifest, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), before)


# --------------------------------------------------------------- 2. plugin-root cache

class TestPluginRootCache(unittest.TestCase):
    def test_record_and_read_round_trip(self):
        with tempfile.TemporaryDirectory() as home, \
                tempfile.TemporaryDirectory() as plugin:
            path = record_plugin_root(plugin, home=home)
            self.assertIsNotNone(path)
            self.assertEqual(path, os.path.join(home, MOKATA_DIR, "plugin-root"))
            self.assertEqual(read_plugin_root(home=home), os.path.abspath(plugin))

    def test_record_is_idempotent(self):
        with tempfile.TemporaryDirectory() as home, \
                tempfile.TemporaryDirectory() as plugin:
            record_plugin_root(plugin, home=home)
            record_plugin_root(plugin, home=home)
            self.assertEqual(read_plugin_root(home=home), os.path.abspath(plugin))

    def test_record_never_raises_on_bad_home(self):
        # A write failure must degrade to None, never raise (the hook can't hard-fail).
        # Use a FILE as the parent directory — makedirs under it fails on every OS (a bare
        # "/nonexistent" root is writable on a Windows CI runner, so it wouldn't fail there).
        with tempfile.TemporaryDirectory() as d:
            blocker = os.path.join(d, "not-a-dir")
            with open(blocker, "w", encoding="utf-8") as fh:
                fh.write("x")
            bogus = os.path.join(blocker, "home")
            self.assertIsNone(record_plugin_root("/whatever", home=bogus))

    def test_read_absent_is_none(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(read_plugin_root(home=home))


# --------------------------------------------------------------- 3. root resolution

class TestProjectRootResolution(unittest.TestCase):
    def test_is_initialized_stable_across_reload(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(Surface.is_initialized(d))
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            self.assertTrue(Surface.is_initialized(d))
            Surface.load(d)  # reload
            self.assertTrue(Surface.is_initialized(d))

    def test_find_project_root_from_subdirectory(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            deep = os.path.join(d, "a", "b", "c")
            os.makedirs(deep)
            root = find_project_root(deep)
            self.assertEqual(os.path.realpath(root), os.path.realpath(d))
            # and so the project reads as initialized from deep inside it
            self.assertTrue(Surface.is_initialized(root))

    def test_find_project_root_uninitialized_returns_start(self):
        with tempfile.TemporaryDirectory() as d:
            # no .mokata anywhere and no .git -> the start dir itself
            self.assertEqual(os.path.realpath(find_project_root(d)),
                             os.path.realpath(d))
            self.assertFalse(Surface.is_initialized(find_project_root(d)))


# --------------------------------------------------------------- 4. SessionStart offer

class TestSessionStartOffer(unittest.TestCase):
    def _run_hook(self, cwd):
        # Isolate from the real ~ so the plugin-root cache writes to a throwaway HOME.
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ)
            env["HOME"] = home
            env.pop("CLAUDE_PLUGIN_ROOT", None)
            proc = subprocess.run(
                [sys.executable, HOOK], input=json.dumps({"cwd": cwd}),
                capture_output=True, text=True, env=env)
            return proc

    def _context(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        return payload["hookSpecificOutput"]["additionalContext"]

    def test_offer_fires_on_fresh_repo(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = self._context(self._run_hook(d))
            self.assertIn("/mokata:init", ctx)
            self.assertNotIn("profile:", ctx)  # not a normal briefing

    def test_offer_does_not_fire_when_initialized_even_on_rerun(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            # simulate two fresh sessions: the hook runs again each time
            for _ in range(2):
                ctx = self._context(self._run_hook(d))
                self.assertNotIn("/mokata:init", ctx)
                self.assertIn("profile:", ctx)  # the real briefing instead

    def test_offer_does_not_fire_from_subdirectory_of_initialized_repo(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=_silent)
            deep = os.path.join(d, "src", "pkg")
            os.makedirs(deep)
            ctx = self._context(self._run_hook(deep))
            self.assertNotIn("/mokata:init", ctx)
            self.assertIn("profile:", ctx)

    def test_hook_records_plugin_root(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            env = dict(os.environ)
            # HOME is POSIX; the hook's `~` resolves via USERPROFILE on Windows — set both so
            # the child writes the plugin-root cache into the test's home on every OS.
            env["HOME"] = home
            env["USERPROFILE"] = home
            env.pop("CLAUDE_PLUGIN_ROOT", None)
            subprocess.run([sys.executable, HOOK], input=json.dumps({"cwd": d}),
                           capture_output=True, text=True, env=env)
            cached = read_plugin_root(home=home)
            self.assertIsNotNone(cached)
            # the hook lives at <root>/hooks/session_start.py -> root holds src/mokata
            self.assertTrue(os.path.isdir(os.path.join(cached, "src", "mokata")))


# --------------------------------------------------------------- 5. MCP init tool

class TestMcpInitTool(unittest.TestCase):
    def setUp(self):
        from mokata import mcp_server
        self.init = mcp_server.init

    def test_propose_only_without_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            res = self.init(path=d, profile="full")
            self.assertEqual(res["status"], "proposed")
            self.assertIn("full", res["preview"])
            self.assertFalse(os.path.exists(os.path.join(d, MOKATA_DIR)))

    def test_confirm_writes_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            res = self.init(path=d, profile="full", confirm=True)
            self.assertTrue(res["committed"])
            self.assertTrue(Surface.is_initialized(d))
            self.assertEqual(Surface.load(d).manifest.profile, "full")

    def test_overwrite_needs_force(self):
        with tempfile.TemporaryDirectory() as d:
            self.init(path=d, profile="standard", confirm=True)
            blocked = self.init(path=d, profile="full", confirm=True)
            self.assertFalse(blocked.get("committed"))
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(Surface.load(d).manifest.profile, "standard")  # unchanged
            ok = self.init(path=d, profile="full", confirm=True, force=True)
            self.assertTrue(ok["committed"])
            self.assertEqual(Surface.load(d).manifest.profile, "full")

    def test_unknown_profile_errors(self):
        with tempfile.TemporaryDirectory() as d:
            res = self.init(path=d, profile="bogus", confirm=True)
            self.assertEqual(res["status"], "error")
            self.assertFalse(os.path.exists(os.path.join(d, MOKATA_DIR)))


class TestInitCommandTemplate(unittest.TestCase):
    def _body(self):
        with open(os.path.join(ROOT, "templates", "commands", "init.md"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_init_command_ships_with_frontmatter(self):
        text = self._body()
        self.assertTrue(text.startswith("---\n"))
        head = text.split("---", 2)[1]
        for key in ("name:", "description:", "argument-hint:", "allowed-tools:"):
            self.assertIn(key, head, f"init.md frontmatter missing {key}")

    def test_init_command_drives_the_gated_engine(self):
        text = self._body()
        # the preview-before-apply order (P2) and the plugin-root discovery are present
        self.assertIn("--preview", text)
        self.assertIn("plugin-root", text)
        self.assertIn("$ARGUMENTS", text)


if __name__ == "__main__":
    unittest.main()
