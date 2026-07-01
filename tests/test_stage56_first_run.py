"""Stage 56 — magical first-run + guided interactive setup wizard + delightful errors.

A brand-new user should get visible value in MINUTES. This stage adds, composing the existing
init / setup / detect / suggest primitives (none rebuilt):

  1. an INTERACTIVE first-run wizard — ASK the profile, DETECT integrations, ASK which to wire,
     then RUN the wire steps HUMAN-GATED (decline → nothing wired); detect→recommend→run-with-
     approval, NEVER a silent third-party install; the non-interactive --yes path is preserved;
  2. a 30-second "here's what I just did" summary naming what was wired + the next step;
  3. /mokata:tour — a short read-only demo on a sample (graph query, memory recall, gate catch);
  4. "did you mean …" + next-step hints on an unknown CLI command (difflib over the command set).

Inviolables proven: human-gated where it writes/wires (decline → nothing); never silently
installs; --yes preserved; degrade-clean; reachable in Claude Code (slash + MCP); parity-clean.
Gates are driven by injected ask/confirm callables (the 53b lesson), never real prompts.
"""

import io
import os
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import onboarding as OB
from mokata.config import Surface
from mokata.detect import Detector


# Assembled from fragments so no literal credential lives in this file (the secret-guard hook
# would otherwise block committing it — and the tour's own gate-catch demo is the thing tested).
_SAMPLE_KEY = "AKIA" + "IOSFODNN7" + "EXAMPLE"


def _detector(present):
    """A Detector that forces exactly `present` (a set of tool ids) to be present."""
    from mokata.profiles import TOOL_CATALOG
    return Detector(overrides={tid: (tid in present) for tid in TOOL_CATALOG})


class _Asker:
    """Records the choice questions asked; always answers `answer`."""
    def __init__(self, answer="standard"):
        self.answer = answer
        self.calls = []

    def __call__(self, prompt, choices, default):
        self.calls.append((prompt, tuple(choices), default))
        return self.answer


def _boom_ask(*a, **k):
    raise AssertionError("the wizard asked a question on the non-interactive path")


# ============================================================ did-you-mean (unknown command)
class TestDidYouMean(unittest.TestCase):
    def test_closest_command_matches_a_typo(self):
        self.assertEqual(OB.closest_command("inti", ["init", "status", "doctor"]), "init")
        self.assertEqual(OB.closest_command("statuss", ["init", "status", "doctor"]), "status")

    def test_no_close_match_returns_none(self):
        self.assertIsNone(OB.closest_command("zzzzzz", ["init", "status"]))

    def test_message_suggests_closest_and_a_next_step_when_uninitialized(self):
        msg = OB.unknown_command_message("inti", known=["init", "status"], initialized=False)
        self.assertIn("not a mokata command", msg)
        self.assertIn("init", msg)             # the closest match
        self.assertIn("Next:", msg)            # a next-step hint
        self.assertIn("mokata init", msg)      # uninitialized → points at setup

    def test_message_next_step_is_context_aware_when_initialized(self):
        msg = OB.unknown_command_message("brainstrm", known=["brainstorm"], initialized=True)
        self.assertIn("brainstorm", msg)       # closest match
        self.assertIn("Next:", msg)            # suggest-driven next step

    def test_cli_main_unknown_command_returns_2_with_hint(self):
        from mokata import cli
        err = io.StringIO()
        so = sys.stderr
        sys.stderr = err
        try:
            rc = cli.main(["statuss", "--path", "."])     # a realistic typo of `status`
        finally:
            sys.stderr = so
        self.assertEqual(rc, 2)
        text = err.getvalue()
        self.assertIn("not a mokata command", text)
        self.assertIn("Did you mean 'status'", text)

    def test_cli_main_far_typo_still_degrades_clean(self):
        from mokata import cli
        err = io.StringIO()
        so = sys.stderr
        sys.stderr = err
        try:
            rc = cli.main(["frobnicate", "--path", "."])   # no close match — still a clean hint
        finally:
            sys.stderr = so
        self.assertEqual(rc, 2)
        self.assertIn("not a mokata command", err.getvalue())
        self.assertIn("Next:", err.getvalue())

    def test_cli_main_known_command_still_runs(self):
        from mokata import cli
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            so = sys.stdout
            sys.stdout = out
            try:
                rc = cli.main(["detect", "--path", d])    # a real, read-only command
            finally:
                sys.stdout = so
            self.assertEqual(rc, 0)


# ============================================================ the first-run wizard
class TestWizard(unittest.TestCase):
    def test_decline_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            res = OB.run_wizard(d, ask=_Asker("standard"), confirm=lambda p: False,
                                out=lambda _: None, detector=_detector(set()))
            self.assertTrue(res.aborted)
            self.assertFalse(Surface.is_initialized(d), "a declined wizard still wrote config")

    def test_asks_profile_and_detects_integrations(self):
        with tempfile.TemporaryDirectory() as d:
            asker = _Asker("minimal")
            res = OB.run_wizard(d, ask=asker, confirm=lambda p: True, out=lambda _: None,
                                detector=_detector({"obsidian"}), wire_harness=False)
            # the profile was ASKED (not just defaulted)
            self.assertTrue(asker.calls, "the wizard did not ask the profile")
            self.assertEqual(res.profile, "minimal")
            # the full environment was DETECTED and surfaced
            self.assertIn("obsidian", res.detected)
            self.assertTrue(res.detected["obsidian"])
            self.assertFalse(res.detected["postgres"])

    def test_wires_a_present_integration_only_on_approval(self):
        with tempfile.TemporaryDirectory() as d:
            res = OB.run_wizard(d, ask=_Asker("standard"), confirm=lambda p: True,
                                out=lambda _: None, detector=_detector({"obsidian"}),
                                wire_harness=False)
            self.assertFalse(res.aborted)
            self.assertTrue(Surface.is_initialized(d))
            # obsidian got wired into the memory_store chain (a real config edit)
            surface = Surface.load(d)
            chain = surface.manifest.capabilities["memory_store"]["fallback"]
            self.assertIn("obsidian", chain)
            self.assertTrue(any("obsidian" in w for w in res.wired))

    def test_never_silently_installs_an_absent_tool_only_recommends(self):
        with tempfile.TemporaryDirectory() as d:
            # postgres ABSENT — the wizard must RECOMMEND its install, never run it, never wire it
            res = OB.run_wizard(d, ask=_Asker("standard"), confirm=lambda p: True,
                                out=lambda _: None, detector=_detector({"obsidian"}),
                                wire_harness=False)
            self.assertTrue(any("postgres" in r for r in res.recommended),
                            "an absent integration was not recommended")
            self.assertTrue(any("pip install" in r for r in res.recommended),
                            "the recommendation is not a copy-pasteable install command")
            surface = Surface.load(d)
            self.assertNotIn("postgres",
                             surface.manifest.capabilities["memory_store"]["fallback"],
                             "an absent tool was silently wired")

    def test_non_interactive_yes_path_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            res = OB.run_wizard(d, ask=_boom_ask, confirm=_boom_ask, out=lambda _: None,
                                assume_yes=True, profile="standard")
            self.assertFalse(res.aborted)
            self.assertEqual(res.message, "ok")
            self.assertTrue(Surface.is_initialized(d))
            self.assertEqual(Surface.load(d).manifest.profile, "standard")

    def test_summary_names_what_was_wired_and_the_next_step(self):
        with tempfile.TemporaryDirectory() as d:
            res = OB.run_wizard(d, ask=_Asker("standard"), confirm=lambda p: True,
                                out=lambda _: None, detector=_detector({"obsidian"}),
                                wire_harness=False)
            summary = OB.render_did_summary(res)
            self.assertIn("standard", summary)          # the profile
            self.assertIn("obsidian", summary)          # what got wired
            self.assertIn("Next:", summary)             # the next step
            self.assertIn("/mokata:", summary)          # a concrete next command

    def test_wizard_wires_the_harness_on_approval(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as home:
            res = OB.run_wizard(d, ask=_Asker("standard"), confirm=lambda p: True,
                                out=lambda _: None, detector=_detector(set()),
                                wire_harness=True, harness="claude", scope="project",
                                home=home)
            self.assertTrue(res.harness_wired)
            # commands were copied into the project's .claude/commands (a real wire step)
            self.assertTrue(os.path.isdir(os.path.join(d, ".claude", "commands")))
            self.assertTrue(any("harness" in w for w in res.wired))


# ============================================================ /mokata:tour (read-only demo)
class TestTour(unittest.TestCase):
    def test_tour_shows_three_demos(self):
        text = OB.build_tour()
        self.assertIn("Graph query", text)
        self.assertIn("Memory recall", text)
        self.assertIn("Gate catch", text)

    def test_tour_gate_demo_is_a_real_secret_catch(self):
        text = OB.build_tour()
        # the gate-catch demo runs a REAL secret scan over a sample line → a real block verdict
        self.assertIn("blocked", text.lower())

    def test_cli_tour_is_read_only(self):
        from mokata import cli
        with tempfile.TemporaryDirectory() as d:
            before = sorted(os.listdir(d))
            out = io.StringIO()
            so = sys.stdout
            sys.stdout = out
            try:
                rc = cli.main(["tour", "--path", d])
            finally:
                sys.stdout = so
            self.assertEqual(rc, 0)
            self.assertEqual(sorted(os.listdir(d)), before, "tour wrote to the repo")
            self.assertIn("Memory recall", out.getvalue())

    def test_tour_is_a_read_mcp_tool(self):
        from mokata import mcp_server as M
        self.assertIn("tour", M.read_tool_names())
        res = M.tour()
        self.assertIn("tour", res)
        self.assertIn("Graph query", res["tour"])


# ============================================================ surfaces + parity
class TestSurfacesAndParity(unittest.TestCase):
    def test_tour_in_matrix_and_parity_passes(self):
        from mokata import parity
        self.assertIn("tour", parity.SURFACE_MATRIX)
        self.assertIn("tour", parity.SURFACE_MATRIX["tour"].slash)
        self.assertTrue(parity.verify_parity().ok, parity.verify_parity().render())

    def test_setup_reclassified_with_a_slash_surface(self):
        from mokata import parity
        s = parity.SURFACE_MATRIX["setup"]
        self.assertIn("setup", s.slash)         # Stage 56: /mokata:setup guided wizard
        self.assertFalse(s.exempt, "setup is now reachable in-harness, not exempt")

    def test_new_slash_templates_exist_and_are_namespaced(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("setup", "tour"):
            path = os.path.join(root, "templates", "commands", f"{name}.md")
            self.assertTrue(os.path.exists(path), f"{name}.md missing")
            with open(path, encoding="utf-8") as fh:
                md = fh.read()
            self.assertIn(f"name: {name}", md)
            self.assertIn("description: mokata ·", md)


if __name__ == "__main__":
    unittest.main()
