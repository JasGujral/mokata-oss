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
# Stage 53b: hooks are wired as `mokata-hook <subcommand>` (console entry point); the
# standalone scripts still ship as shims for the launch.sh fallback.
HOOK_SUBCOMMANDS = ("session-start", "secret-guard")
HOOK_SHIMS = ("session_start.py", "secret_guard.py")


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
        # Stage 3: the hooks tree moved INTO the package (src/mokata/hooks/) so it ships in
        # the wheel, so it is NO LONGER at the plugin root where Claude Code auto-loads
        # `hooks/hooks.json`. The manifest MUST therefore reference it explicitly via the
        # "hooks" key (a relative-to-plugin-root path). Because there is no root
        # `hooks/hooks.json` to auto-load, this does NOT trigger the "Duplicate hooks file
        # detected" error (that fires only when BOTH the root file AND the key exist).
        data = json.loads(read(".claude-plugin/plugin.json"))
        self.assertIn(
            "hooks", data,
            "manifest must reference the packaged hooks.json (no root auto-load after Stage 3)")
        hooks_rel = data["hooks"].lstrip("./")
        self.assertTrue(os.path.exists(os.path.join(ROOT, hooks_rel)),
                        f"declared hooks path {data['hooks']} does not resolve to a file")
        hooks_json = json.loads(read(hooks_rel))
        blob = json.dumps(hooks_json)
        # Stage 53b: wired via the `mokata-hook` console entry point (no bare python3/sh).
        # HOOK-RESOLVE: and no longer by BARE name — the static plugin manifest invokes the
        # self-resolving shim under ${CLAUDE_PLUGIN_ROOT}, which execs `mokata-hook` once it
        # resolves and fails LOUD (exit 1) when it cannot, instead of the hook being dropped.
        for sub in HOOK_SUBCOMMANDS:
            self.assertIn(f"mokata-hook-launch\\\" {sub}", blob,
                          f"hooks.json does not wire the shim for `{sub}`")
        for shim in HOOK_SHIMS:                 # the fallback shims still ship
            self.assertTrue(os.path.exists(os.path.join(ROOT, "src", "mokata", "hooks", shim)))
        # the security hook is wired on a tool-use event; session-start on session start
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
                  ".github/workflows/codeql.yml", ".github/dependabot.yml",
                  ".github/CODEOWNERS",
                  "mkdocs.yml"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, f)), f"missing {f}")

    def test_readme_has_no_build_jargon(self):
        readme = read("README.md")
        self.assertNotIn("docs/build", readme)
        self.assertNotIn("Stage 0", readme)


if __name__ == "__main__":
    unittest.main()
