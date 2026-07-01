"""Stage 64b — Copilot deep integration (builds on the Stage-64 VS Code extension).

Two pieces, both reusing the Stage-64 thin client + the existing mokata CLI/MCP:
  1. an `@mokata` Copilot Chat PARTICIPANT (VS Code Chat API) — read-only intents only;
  2. wiring the bundled `mokata-mcp` server into Copilot Chat's MCP config.

A chat participant / MCP wiring can't run in the Python unittest suite, so these tests validate
the scaffold WITHOUT touching the Python core: package.json declares the `chatParticipants`
contribution; the participant is READ-ONLY by construction (its intent map targets only the
Stage-64 READ_COMMANDS whitelist — no durable-write subcommand is wired); the `mokata-mcp`
Copilot MCP config snippet is valid JSON and names `mokata-mcp`; the degrade-clean copy is
present. The real Copilot Chat run is the documented MANUAL-VERIFICATION leg (see the how-to).
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


# The Stage-64 read-only whitelist (mokata.ts READ_COMMANDS keys) — chat may target ONLY these.
_READ_VIEWS = {"status", "progress", "governance", "memory"}
# Durable-write / side-effecting verbs the participant must NEVER spawn — it proposes & defers.
_WRITE_FORBIDDEN = {
    "init", "setup", "unsetup", "reset", "remember", "ship", "spec", "develop",
    "import", "export", "push", "pull", "session", "vault", "reconfigure", "upgrade",
    "spec-check", "skill",
}


class TestChatScaffoldExists(unittest.TestCase):
    def test_chat_module_present(self):
        self.assertTrue(os.path.isfile(os.path.join(_EXT, "src", "chat.ts")),
                        "missing src/chat.ts (the pure intent->read-view mapping)")

    def test_mcp_config_snippet_present(self):
        self.assertTrue(os.path.isfile(os.path.join(_EXT, "mcp", "mokata.mcp.json")),
                        "missing mcp/mokata.mcp.json (the Copilot/VS Code MCP config snippet)")


class TestPackageDeclaresChatParticipant(unittest.TestCase):
    def setUp(self):
        self.pkg = json.loads(_read("package.json"))

    def test_chat_participants_contribution(self):
        parts = self.pkg["contributes"].get("chatParticipants")
        self.assertTrue(parts, "package.json must declare contributes.chatParticipants")
        names = {p["name"] for p in parts}
        self.assertIn("mokata", names, "the @mokata participant is not declared")

    def test_participant_subcommands_are_read_only(self):
        parts = self.pkg["contributes"]["chatParticipants"]
        mokata = next(p for p in parts if p["name"] == "mokata")
        sub = {c["name"] for c in mokata.get("commands", [])}
        # the declared chat subcommands (status/progress/memory/why…) must be read intents only
        self.assertEqual(sub & _WRITE_FORBIDDEN, set(),
                         "a write subcommand is declared on the chat participant")
        self.assertTrue(sub, "the participant declares no subcommands")

    def test_engine_supports_chat_api(self):
        # the finalized Chat API requires VS Code >= 1.90
        eng = self.pkg["engines"]["vscode"]
        m = re.search(r"(\d+)\.(\d+)", eng)
        self.assertIsNotNone(m)
        major, minor = int(m.group(1)), int(m.group(2))
        self.assertTrue((major, minor) >= (1, 90),
                        f"engines.vscode {eng} is below 1.90 (Chat API)")

    def test_setup_mcp_command_declared(self):
        cmds = {c["command"] for c in self.pkg["contributes"]["commands"]}
        self.assertIn("mokata.setupCopilotMcp", cmds,
                      "the one-step Copilot MCP setup command is not declared")


class TestParticipantReadOnlyByConstruction(unittest.TestCase):
    def test_chat_read_views_target_only_the_whitelist(self):
        src = _read("src", "chat.ts")
        m = re.search(r"CHAT_READ_VIEWS[^=]*=\s*\{([^}]*)\}", src, re.DOTALL)
        self.assertIsNotNone(m, "CHAT_READ_VIEWS map not found in chat.ts")
        targets = set(re.findall(r":\s*['\"]([a-z]+)['\"]", m.group(1)))
        self.assertTrue(targets, "no read views parsed from CHAT_READ_VIEWS")
        self.assertTrue(targets <= _READ_VIEWS,
                        f"chat targets a non-whitelisted view: {targets - _READ_VIEWS}")
        self.assertEqual(targets & _WRITE_FORBIDDEN, set())

    def test_chat_module_does_not_spawn_the_cli(self):
        # chat.ts is the pure mapping; it must not import/spawn a process itself — all reads go
        # through runRead in mokata.ts (the single guarded spawn site).
        src = _read("src", "chat.ts")
        for bad in ("child_process", "execFile(", "execSync(", "spawnSync("):
            self.assertNotIn(bad, src, f"chat.ts spawns a process ({bad}); route via mokata.ts")

    def test_write_intents_propose_and_defer(self):
        src = _read("src", "chat.ts")
        # a write verb resolves to a 'propose' intent (defer to the human-gated CLI), not a run
        self.assertIn("propose", src)
        self.assertRegex(src, r"WRITE_VERBS", "no write-verb deferral list in chat.ts")

    def test_extension_chat_handler_uses_runRead_not_a_spawn(self):
        ext = _read("src", "extension.ts")
        # the Stage-64 invariant holds: extension.ts never spawns the CLI directly
        for bad in ("child_process", "execFile(", "execSync(", "spawnSync("):
            self.assertNotIn(bad, ext)
        # the chat participant is wired and degrades cleanly where the Chat API is absent
        self.assertIn("createChatParticipant", ext)

    def test_chat_button_defers_to_terminal_passthrough(self):
        # the write path offers a button that STAGES the command in a terminal (human runs it)
        ext = _read("src", "extension.ts")
        self.assertIn("mokata.runInTerminal", ext)


class TestMcpConfigSnippet(unittest.TestCase):
    def test_snippet_is_valid_json_and_names_mokata_mcp(self):
        cfg = json.loads(_read("mcp", "mokata.mcp.json"))
        servers = cfg.get("servers", {})
        self.assertIn("mokata", servers, "the mcp snippet doesn't register a 'mokata' server")
        self.assertEqual(servers["mokata"]["command"], "mokata-mcp",
                         "the mokata server must launch the bundled `mokata-mcp` console entry")

    def test_chat_ts_embeds_a_matching_mcp_server_definition(self):
        src = _read("src", "chat.ts")
        self.assertIn("mokata-mcp", src,
                      "chat.ts should carry the mokata-mcp server definition for the setup command")


class TestDegradeCleanCopy(unittest.TestCase):
    def test_friendly_copy_present(self):
        src = (_read("src", "chat.ts") + _read("src", "extension.ts")).lower()
        # mokata absent / not initialized -> the Stage-64 runRead messages flow through; the
        # chat layer also has copy for the Chat API being unavailable.
        self.assertIn("read-only", src)
        self.assertTrue("not available" in src or "not supported" in src or "couldn't" in src,
                        "no degrade copy for an unavailable Chat/MCP surface")


class TestHonestScope(unittest.TestCase):
    def test_readme_documents_copilot_and_manual_verification(self):
        readme = _read("README.md").lower()
        self.assertIn("copilot", readme)
        # MCP wiring is honest about the user step
        self.assertIn("mcp", readme)


if __name__ == "__main__":
    unittest.main()
