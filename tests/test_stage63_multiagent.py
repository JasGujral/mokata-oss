"""Stage 63 — multi-agent / multi-harness breadth (the Reach anchor).

mokata runs under MORE agents than Claude Code — Cursor, GitHub Copilot, Windsurf, Gemini
CLI, Aider — behind the EXISTING harness boundary (Stage 52 pattern, not rebuilt). The
inviolable: NEVER pretend a capability — an unverified/unsupported capability is declared
ABSENT so the HarnessBoundary degrades CLEARLY (ok=False/degraded, names the missing
capability), never a silent gate no-op. Setup is human-gated, idempotent, reversible
(no residue), and maps the /mokata: command set to each agent's NATIVE surface. The claude
wiring stays byte-compatible.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path-fix side-effect)

from mokata.cli import main
from mokata.harness import (
    HARNESS_CAPABILITIES,
    HarnessBoundary,
    aider_harness,
    available_harnesses,
    capability_matrix,
    copilot_harness,
    cursor_harness,
    gemini_harness,
    get_harness,
    windsurf_harness,
)
from mokata.harness_setup import (
    resolve_targets,
    setup_harness,
    unsetup_harness,
)

NEW_AGENTS = ("cursor", "copilot", "windsurf", "gemini", "aider")


def run_cli(argv):
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with redirect_stdout(buf):
            rc = main(argv)
    finally:
        sys.stdin = old
    return rc, buf.getvalue()


def silent(_):
    pass


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ======================================================================================
# Capability honesty — each adapter declares ONLY what it really supports.
# ======================================================================================
class TestCapabilityHonesty(unittest.TestCase):
    # the VERIFIED capability set for each new agent (commands/hooks/ctx/subagents)
    EXPECTED = {
        "cursor":   {"commands": True,  "hooks": False, "context_injection": True,  "subagents": False},
        "copilot":  {"commands": True,  "hooks": False, "context_injection": True,  "subagents": False},
        "windsurf": {"commands": True,  "hooks": False, "context_injection": True,  "subagents": False},
        "gemini":   {"commands": True,  "hooks": False, "context_injection": True,  "subagents": False},
        "aider":    {"commands": False, "hooks": False, "context_injection": True,  "subagents": False},
    }

    def test_each_new_agent_is_registered(self):
        names = available_harnesses()
        for a in NEW_AGENTS:
            self.assertIn(a, names)

    def test_declared_capabilities_match_the_verified_set(self):
        m = capability_matrix()
        for agent, caps in self.EXPECTED.items():
            for cap, want in caps.items():
                self.assertEqual(m[agent][cap], want,
                                 f"{agent}.{cap} should be {want} (honest declaration)")

    def test_no_agent_claims_hooks_or_subagents_it_cannot_drive(self):
        # The Stage-52 inviolable: when unsure, declare absent. None of the new agents has a
        # mokata-drivable PreToolUse hook or subagent fan-out — so all declare both absent.
        m = capability_matrix()
        for agent in NEW_AGENTS:
            self.assertFalse(m[agent]["hooks"])
            self.assertFalse(m[agent]["subagents"])

    def test_factories_construct_named_harnesses(self):
        for factory, name in ((cursor_harness, "cursor"), (copilot_harness, "copilot"),
                              (windsurf_harness, "windsurf"), (gemini_harness, "gemini"),
                              (aider_harness, "aider")):
            self.assertEqual(factory().name, name)


# ======================================================================================
# Degrade-clean — a lacked capability degrades CLEARLY, never a silent gate no-op.
# ======================================================================================
class TestBoundaryDegradesClearly(unittest.TestCase):
    def test_lacked_capability_names_itself_and_the_harness(self):
        # hooks: every new agent lacks it -> run_hook degrades, naming "hooks" + the agent.
        for agent in NEW_AGENTS:
            b = HarnessBoundary(get_harness(agent))
            r = b.run_hook("secret-guard")
            self.assertFalse(r.ok, f"{agent} pretended to run a hook")
            self.assertTrue(r.degraded)
            self.assertIn("hooks", r.message)
            self.assertIn(agent, r.message)

    def test_subagents_degrade_on_every_new_agent(self):
        for agent in NEW_AGENTS:
            r = HarnessBoundary(get_harness(agent)).run_subagent("t1")
            self.assertFalse(r.ok)
            self.assertIn("subagents", r.message)

    def test_aider_lacks_commands_and_says_so(self):
        r = HarnessBoundary(get_harness("aider")).run_command("brainstorm")
        self.assertFalse(r.ok)
        self.assertTrue(r.degraded)
        self.assertIn("commands", r.message)
        self.assertIn("aider", r.message)

    def test_supported_capabilities_still_work(self):
        # context_injection is supported by all new agents -> ok, not degraded.
        for agent in NEW_AGENTS:
            r = HarnessBoundary(get_harness(agent)).inject_context("briefing")
            self.assertTrue(r.ok, f"{agent} context injection should work")
            self.assertFalse(r.degraded)
        # commands work on the four that support them (not aider)
        for agent in ("cursor", "copilot", "windsurf", "gemini"):
            self.assertTrue(HarnessBoundary(get_harness(agent)).run_command("spec").ok)


# ======================================================================================
# Setup wiring — native command surface + MCP where supported; human-gated, idempotent.
# ======================================================================================
class TestSetupWiring(unittest.TestCase):
    # (agent, commands_subpath, a sample native filename, mcp auto-wired?)
    CASES = [
        ("cursor",   os.path.join(".cursor", "commands"),    "brainstorm.md",        True),
        ("copilot",  os.path.join(".github", "prompts"),     "brainstorm.prompt.md", False),
        ("windsurf", os.path.join(".windsurf", "workflows"), "brainstorm.md",        False),
        ("gemini",   os.path.join(".gemini", "commands"),    "brainstorm.toml",      True),
        ("aider",    os.path.join(".aider", "mokata-commands"), "brainstorm.md",     False),
    ]

    def test_setup_writes_each_agents_native_command_files(self):
        for agent, subpath, sample, _mcp in self.CASES:
            with self.subTest(agent=agent):
                d = tempfile.mkdtemp()
                res = setup_harness(agent, root=d, assume_yes=True, out=silent)
                self.assertFalse(res.aborted)
                cdir = os.path.join(d, subpath)
                self.assertTrue(os.path.isdir(cdir), f"{agent} command dir missing")
                self.assertTrue(os.path.exists(os.path.join(cdir, sample)),
                                f"{agent} native file {sample} not written")

    def test_mcp_auto_wired_only_where_the_schema_matches(self):
        for agent, _sub, _sample, mcp_auto in self.CASES:
            with self.subTest(agent=agent):
                d = tempfile.mkdtemp()
                setup_harness(agent, root=d, assume_yes=True, out=silent)
                t = resolve_targets("project", d, harness=agent)
                if mcp_auto:
                    self.assertIsNotNone(t.mcp_path)
                    data = read_json(t.mcp_path)
                    self.assertIn("mokata", data.get("mcpServers", {}),
                                  f"{agent} should auto-register the mokata MCP server")
                else:
                    self.assertIsNone(t.mcp_path,
                                      f"{agent} must NOT auto-wire MCP (manual step)")

    def test_gemini_toml_is_well_formed(self):
        d = tempfile.mkdtemp()
        setup_harness("gemini", root=d, assume_yes=True, out=silent)
        toml = read_text(os.path.join(d, ".gemini", "commands", "brainstorm.toml"))
        self.assertIn("description = \"", toml)
        self.assertIn("prompt = '''", toml)
        # the gemini MCP merges into settings.json, preserving the file
        settings = read_json(os.path.join(d, ".gemini", "settings.json"))
        self.assertEqual(settings["mcpServers"]["mokata"]["command"], "mokata-mcp")

    def test_setup_is_human_gated(self):
        # A declined confirm writes NOTHING (P2 — durable writes are human-gated).
        for agent in NEW_AGENTS:
            with self.subTest(agent=agent):
                d = tempfile.mkdtemp()
                res = setup_harness(agent, root=d, confirm=lambda _p: False, out=silent)
                self.assertTrue(res.aborted)
                self.assertEqual(res.touched, [])

    def test_setup_is_idempotent(self):
        for agent, subpath, sample, _mcp in self.CASES:
            with self.subTest(agent=agent):
                d = tempfile.mkdtemp()
                setup_harness(agent, root=d, assume_yes=True, out=silent)
                cdir = os.path.join(d, subpath)
                first = sorted(os.listdir(cdir))
                setup_harness(agent, root=d, assume_yes=True, out=silent)  # re-run converges
                self.assertEqual(sorted(os.listdir(cdir)), first)
                t = resolve_targets("project", d, harness=agent)
                if t.mcp_path is not None:
                    data = read_json(t.mcp_path)
                    # exactly one mokata entry — no duplication on re-run
                    self.assertIn("mokata", data.get("mcpServers", {}))

    def test_unsetup_leaves_no_residue(self):
        for agent, subpath, _sample, _mcp in self.CASES:
            with self.subTest(agent=agent):
                d = tempfile.mkdtemp()
                setup_harness(agent, root=d, assume_yes=True, out=silent)
                unsetup_harness(agent, root=d, assume_yes=True, out=silent)
                cdir = os.path.join(d, subpath)
                left = os.listdir(cdir) if os.path.isdir(cdir) else []
                self.assertEqual(left, [], f"{agent} left command residue: {left}")
                t = resolve_targets("project", d, harness=agent)
                if t.mcp_path is not None and os.path.exists(t.mcp_path):
                    data = read_json(t.mcp_path)
                    self.assertNotIn("mokata", data.get("mcpServers", {}),
                                     f"{agent} left an MCP entry behind")

    def test_plan_render_states_what_degrades(self):
        # The setup plan must NAME the capabilities the agent lacks (clear degrade, no pretense).
        from mokata.harness_setup import plan_setup, render_setup_plan
        d = tempfile.mkdtemp()
        text = render_setup_plan(plan_setup("aider", root=d))
        self.assertIn("lacks", text)
        self.assertIn("commands", text)            # aider's missing native slash commands
        self.assertIn("native surface", text)


# ======================================================================================
# claude byte-compatibility — no regression to the reference wiring.
# ======================================================================================
class TestClaudeByteCompatible(unittest.TestCase):
    def test_claude_command_files_are_byte_identical_to_templates(self):
        from mokata.harness_setup import _templates_dir
        d = tempfile.mkdtemp()
        setup_harness("claude", root=d, with_hooks=False, assume_yes=True, out=silent)
        cdir = os.path.join(d, ".claude", "commands")
        tdir = str(_templates_dir())
        for name in ("brainstorm.md", "ship.md", "spec.md"):
            with open(os.path.join(cdir, name), "rb") as a, \
                    open(os.path.join(tdir, name), "rb") as b:
                self.assertEqual(a.read(), b.read(), f"claude {name} drifted from template")

    def test_claude_still_wires_hooks_and_mcp(self):
        d = tempfile.mkdtemp()
        setup_harness("claude", root=d, assume_yes=True, out=silent)
        self.assertTrue(os.path.exists(os.path.join(d, ".claude", "settings.json")))
        mcp = read_json(os.path.join(d, ".mcp.json"))
        self.assertIn("mokata", mcp["mcpServers"])
        m = capability_matrix()
        self.assertTrue(all(m["claude"].values()))   # claude still supports everything


# ======================================================================================
# CLI surfaces — `mokata harness` lists all + matrix; setup/unsetup per agent; parity green.
# ======================================================================================
class TestCliSurfaces(unittest.TestCase):
    def test_harness_lists_every_agent_with_the_matrix(self):
        rc, out = run_cli(["harness"])
        self.assertEqual(rc, 0)
        for agent in ("claude", "codex", "cowork", *NEW_AGENTS):
            self.assertIn(agent, out)
        # the matrix shows the degrade plainly for a new agent
        block = out[out.index("'cursor'"):]
        self.assertIn("[no ] hooks", block)
        self.assertIn("[yes] commands", block)

    def test_harness_show_one_agent(self):
        rc, out = run_cli(["harness", "gemini"])
        self.assertEqual(rc, 0)
        self.assertIn("gemini", out)
        self.assertIn("[no ] subagents", out)

    def test_setup_and_unsetup_each_agent_via_cli(self):
        for agent in NEW_AGENTS:
            with self.subTest(agent=agent):
                d = tempfile.mkdtemp()
                rc, _ = run_cli(["setup", agent, "--path", d, "--yes"])
                self.assertEqual(rc, 0, f"setup {agent} failed")
                rc, _ = run_cli(["unsetup", agent, "--path", d, "--yes"])
                self.assertEqual(rc, 0, f"unsetup {agent} failed")

    def test_parity_guard_is_green(self):
        from mokata.parity import verify_parity
        report = verify_parity()
        self.assertTrue(report.ok, report.render())


if __name__ == "__main__":
    unittest.main()
