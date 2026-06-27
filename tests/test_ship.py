"""Stage 19A — ship-artifact checks: version consistency, plugin references all commands
and both hooks, and the OSS/CI/docs files are present."""

import json
import os
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import __version__
from mokata.packaging import validate_marketplace, validate_plugin

ROOT = os.path.join(os.path.dirname(__file__), "..")
VERSION = __version__   # canonical; every other location must match it (version-agnostic)

COMMANDS = ("brainstorm", "spec", "test", "develop", "review", "debug", "optimize",
            "bug", "init", "ship", "vault", "onboard")
HOOKS = ("session_start.py", "secret_guard.py")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class TestVersionConsistency(unittest.TestCase):
    def test_version_consistent_everywhere(self):
        self.assertIn(f'version = "{VERSION}"', read("pyproject.toml"))
        self.assertEqual(json.loads(read(".claude-plugin/plugin.json"))["version"],
                         VERSION)
        mp = json.loads(read(".claude-plugin/marketplace.json"))
        self.assertEqual(mp["metadata"]["version"], VERSION)
        self.assertEqual(mp["plugins"][0]["version"], VERSION)
        self.assertIn(f"## [{VERSION}]", read("CHANGELOG.md"))


class TestPluginReferences(unittest.TestCase):
    def test_plugin_and_marketplace_validate(self):
        self.assertEqual(validate_plugin(json.loads(read(".claude-plugin/plugin.json"))),
                         [])
        self.assertEqual(
            validate_marketplace(json.loads(read(".claude-plugin/marketplace.json"))), [])

    def test_plugin_references_all_commands(self):
        data = json.loads(read(".claude-plugin/plugin.json"))
        commands_dir = data["commands"].lstrip("./")
        for cmd in COMMANDS:
            self.assertTrue(
                os.path.exists(os.path.join(ROOT, commands_dir, f"{cmd}.md")),
                f"missing command template {cmd}.md")

    def test_plugin_references_both_hooks(self):
        # Claude Code auto-loads the standard hooks/hooks.json, so the manifest must
        # NOT re-reference it via a "hooks" key — doing so triggers a "Duplicate hooks
        # file detected" error on plugin load. The hooks still load; we validate the
        # auto-loaded file directly here.
        data = json.loads(read(".claude-plugin/plugin.json"))
        self.assertNotIn(
            "hooks", data,
            "manifest must not reference hooks/hooks.json (it auto-loads)")
        hooks_ref = "hooks/hooks.json"
        hooks_json = json.loads(read(hooks_ref))
        blob = json.dumps(hooks_json)
        for hook in HOOKS:
            self.assertIn(hook, blob, f"hooks.json does not reference {hook}")
            self.assertTrue(os.path.exists(os.path.join(ROOT, "hooks", hook)))
        # the security hook is wired on a tool-use event; session_start on session start
        self.assertIn("PreToolUse", blob)
        self.assertIn("SessionStart", blob)


class TestOssAndCiFiles(unittest.TestCase):
    def test_oss_files_present(self):
        for f in ("README.md", "LICENSE", "NOTICE", "CONTRIBUTING.md",
                  "CODE_OF_CONDUCT.md", "SECURITY.md", "CHANGELOG.md",
                  ".github/PULL_REQUEST_TEMPLATE.md",
                  ".github/ISSUE_TEMPLATE/bug_report.yml",
                  ".github/ISSUE_TEMPLATE/feature_request.yml",
                  ".github/ISSUE_TEMPLATE/config.yml",
                  ".github/workflows/ci.yml", ".github/workflows/docs.yml",
                  "mkdocs.yml"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, f)), f"missing {f}")

    def test_readme_has_no_build_jargon(self):
        readme = read("README.md")
        self.assertNotIn("docs/build", readme)
        self.assertNotIn("Stage 0", readme)


if __name__ == "__main__":
    unittest.main()
