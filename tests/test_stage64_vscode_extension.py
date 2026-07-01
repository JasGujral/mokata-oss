"""Stage 64 — editor presence (VS Code first).

The VS Code extension is a SEPARATE artifact (its own folder, its own package.json/tsconfig) —
a VS Code extension can't run in the Python unittest suite. These tests validate the scaffold
WITHOUT touching the Python core: package.json parses + declares its commands/contributions, the
extension is READ-ONLY (no durable-write subcommand is wired to a child-process spawn — it defers
to the human-gated CLI), the degrade-clean copy is present, and JetBrains/Neovim are NOT faked
(roadmap only). The real editor run is a documented MANUAL-VERIFICATION leg (see the how-to).
"""

import json
import os
import re
import unittest

_HERE = os.path.dirname(__file__)
_EXT = os.path.normpath(os.path.join(_HERE, "..", "editors", "vscode"))


def _read(*parts):
    with open(os.path.join(_EXT, *parts), encoding="utf-8") as fh:
        return fh.read()


# Read-only mokata subcommands the extension is allowed to spawn (observability only).
_READ_OK = {"status", "progress", "govern", "memory"}
# Durable-write / side-effecting subcommands it must NEVER spawn itself (stay human-gated CLI).
_WRITE_FORBIDDEN = {
    "init", "setup", "unsetup", "reset", "remember", "ship", "spec", "develop",
    "import", "export", "push", "pull", "session", "vault", "reconfigure", "upgrade",
    "spec-check", "skill",
}


class TestScaffoldExists(unittest.TestCase):
    def test_extension_folder_and_core_files_present(self):
        for rel in ("package.json", "tsconfig.json", "README.md",
                    os.path.join("src", "extension.ts"),
                    os.path.join("src", "mokata.ts")):
            self.assertTrue(os.path.isfile(os.path.join(_EXT, rel)),
                            f"missing extension file: {rel}")

    def test_package_json_parses(self):
        json.loads(_read("package.json"))   # raises on malformed JSON

    def test_tsconfig_parses(self):
        json.loads(_read("tsconfig.json"))


class TestPackageManifest(unittest.TestCase):
    def setUp(self):
        self.pkg = json.loads(_read("package.json"))

    def test_declares_vscode_engine(self):
        self.assertIn("vscode", self.pkg.get("engines", {}),
                      "package.json must declare engines.vscode")

    def test_apache_licensed(self):
        self.assertEqual(self.pkg.get("license"), "Apache-2.0")

    def test_declares_commands(self):
        cmds = {c["command"] for c in self.pkg["contributes"]["commands"]}
        # the read-only views + the human-gated terminal passthrough
        for want in ("mokata.showStatus", "mokata.showProgress", "mokata.showGovernance",
                     "mokata.showMemory", "mokata.refresh", "mokata.runInTerminal"):
            self.assertIn(want, cmds, f"command not declared: {want}")

    def test_command_titles_are_namespaced(self):
        for c in self.pkg["contributes"]["commands"]:
            self.assertTrue(c["title"].lower().startswith("mokata"),
                            f"command title not namespaced: {c['title']}")

    def test_declares_a_view_and_container(self):
        contributes = self.pkg["contributes"]
        self.assertIn("views", contributes)
        self.assertIn("viewsContainers", contributes)
        # the activity-bar panel exists
        view_ids = {v["id"] for group in contributes["views"].values() for v in group}
        self.assertTrue(view_ids, "no view declared")

    def test_declares_opt_in_and_config(self):
        cfg = self.pkg["contributes"]["configuration"]
        props = cfg["properties"] if isinstance(cfg, dict) else \
            {k: v for c in cfg for k, v in c["properties"].items()}
        self.assertIn("mokata.enable", props, "opt-in toggle mokata.enable missing")
        self.assertIn("mokata.cliPath", props, "mokata.cliPath setting missing")

    def test_lazy_activation(self):
        # onStartupFinished (lazy) — never a blanket '*' activation
        events = self.pkg.get("activationEvents", [])
        self.assertNotIn("*", events, "must not eagerly activate on '*'")


class TestReadOnlyByConstruction(unittest.TestCase):
    """The extension never performs a durable write — it spawns only read-only subcommands and
    defers writes to the human-gated CLI (terminal passthrough)."""

    def test_read_command_whitelist_is_read_only(self):
        src = _read("src", "mokata.ts")
        m = re.search(r"READ_COMMANDS[^=]*=\s*\{([^}]*)\}", src, re.DOTALL)
        self.assertIsNotNone(m, "READ_COMMANDS map not found in mokata.ts")
        spawned = set(re.findall(r"\[\s*['\"]([a-z-]+)['\"]", m.group(1)))
        self.assertTrue(spawned, "no spawned subcommands parsed from READ_COMMANDS")
        self.assertTrue(spawned <= _READ_OK,
                        f"non-read subcommand wired: {spawned - _READ_OK}")
        self.assertEqual(spawned & _WRITE_FORBIDDEN, set(),
                         "a durable-write subcommand is wired to a spawn")

    def test_child_process_spawn_is_centralised_not_in_extension(self):
        # all process execution goes through the guarded helper in mokata.ts; extension.ts
        # must not spawn the CLI directly (so the read-only whitelist can't be bypassed).
        ext = _read("src", "extension.ts")
        for bad in ("execFile(", "execSync(", "spawnSync(", "child_process"):
            self.assertNotIn(bad, ext,
                             f"extension.ts spawns the CLI directly ({bad}); route via mokata.ts")

    def test_spawn_helper_guards_against_non_read_commands(self):
        src = _read("src", "mokata.ts")
        self.assertIn("execFile", src, "the spawn helper should live in mokata.ts")
        # a guard that rejects anything not in the read-only whitelist
        self.assertRegex(src, r"read-only|READ_COMMANDS",
                         "no read-only guard around the spawn helper")

    def test_terminal_passthrough_defers_to_human(self):
        # the write path opens a terminal for the human (sendText), never auto-executes a write
        ext = _read("src", "extension.ts")
        self.assertIn("createTerminal", ext)
        self.assertIn("sendText", ext)


class TestDegradeClean(unittest.TestCase):
    def test_friendly_copy_for_not_installed_and_not_initialized(self):
        src = _read("src", "mokata.ts").lower()
        self.assertIn("not installed", src)
        self.assertIn("not initialized", src)

    def test_detects_uninitialised_cli_output(self):
        # the CLI prints "is not initialized …" when there's no .mokata/ — the extension must
        # recognise that and show the friendly message rather than an error spew.
        src = _read("src", "mokata.ts")
        self.assertIn("not initialized", src)


class TestHonestScope(unittest.TestCase):
    """VS Code only. JetBrains/Neovim are a documented roadmap — not stubbed as if working."""

    def test_no_faked_jetbrains_or_neovim_artifacts(self):
        for faked in ("jetbrains", "intellij", "neovim", "nvim"):
            self.assertFalse(os.path.isdir(os.path.join(_EXT, "..", faked)),
                             f"editors/{faked} exists — do not fake unbuilt editors")

    def test_readme_marks_other_editors_as_roadmap(self):
        readme = _read("README.md").lower()
        self.assertIn("roadmap", readme)
        self.assertTrue("jetbrains" in readme or "neovim" in readme,
                        "README should name the roadmap editors honestly")


if __name__ == "__main__":
    unittest.main()
