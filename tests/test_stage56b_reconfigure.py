"""Stage 56b — re-runnable reconfigure wizard (update integrations later).

The SAME guided Q&A as Stage 56, re-runnable any time on an ALREADY-INITIALIZED repo to change
what's wired — add/remove an integration, switch a backend, change profile, re-detect a
newly-installed tool — composing the existing primitives (Stage-56 wizard + setup/unsetup/config),
NONE rebuilt.

Inviolables proven here:
  * HUMAN-GATED on every change (decline → nothing changes);
  * IDEMPOTENT (re-running with no changes is a no-op — converges, no duplicate writes);
  * REVERSIBLE (removing an integration leaves NO residue — gone from the chain AND from tools);
  * NEVER silently installs a third-party tool (absent → recommended, not installed);
  * re-detect picks up a newly-present tool; profile/backend switches go through the gate;
  * the non-interactive --yes path is preserved;
  * the 54e parity guard still passes.

Gates are driven by injected ask/confirm callables (the 53b lesson), never real prompts.
"""

import io
import json
import os
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import onboarding as OB
from mokata.config import Surface
from mokata.detect import Detector
from mokata.profiles import TOOL_CATALOG


def _detector(present):
    """A Detector forcing exactly the tool ids in `present` to be present."""
    return Detector(overrides={tid: (tid in present) for tid in TOOL_CATALOG})


def _init(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _chain(d, need="memory_store"):
    from mokata import config_cmd
    _found, val = config_cmd.config_get(d, f"capabilities.{need}.fallback")
    return val if isinstance(val, list) else []


def _manifest_bytes(d):
    from mokata import MOKATA_DIR, MANIFEST_FILENAME
    with open(os.path.join(d, MOKATA_DIR, MANIFEST_FILENAME), "rb") as fh:
        return fh.read()


def _yes(_):
    return True


def _no(_):
    return False


def _boom(*a, **k):
    raise AssertionError("a prompt was issued on the non-interactive path")


# ============================================================ idempotent no-op
class TestNoOp(unittest.TestCase):
    def test_rerun_with_no_changes_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            before = _manifest_bytes(d)
            res = OB.run_reconfigure(d, assume_yes=True, detector=_detector(set()))
            self.assertTrue(res.initialized)
            self.assertFalse(res.changed)
            self.assertEqual(_manifest_bytes(d), before, "a no-op reconfigure rewrote the manifest")

    def test_adding_an_already_wired_integration_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            OB.run_reconfigure(d, add=["obsidian"], assume_yes=True,
                               detector=_detector({"obsidian"}))
            before = _manifest_bytes(d)
            res = OB.run_reconfigure(d, add=["obsidian"], assume_yes=True,
                                     detector=_detector({"obsidian"}))
            self.assertFalse(res.changed)
            self.assertEqual(_manifest_bytes(d), before, "re-adding rewrote the manifest")


# ============================================================ add (gated)
class TestAdd(unittest.TestCase):
    def test_add_present_integration_on_approval(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            res = OB.run_reconfigure(d, add=["postgres"], confirm=_yes, out=lambda _: None,
                                     detector=_detector({"postgres"}))
            self.assertTrue(res.changed)
            self.assertIn("postgres", res.added)
            self.assertIn("postgres", _chain(d, "memory_store"))

    def test_add_declined_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            before = _manifest_bytes(d)
            res = OB.run_reconfigure(d, add=["postgres"], confirm=_no, out=lambda _: None,
                                     detector=_detector({"postgres"}))
            self.assertTrue(res.aborted)
            self.assertNotIn("postgres", _chain(d, "memory_store"))
            self.assertEqual(_manifest_bytes(d), before)


# ============================================================ remove (reversible / no residue)
class TestRemove(unittest.TestCase):
    def test_remove_unwinds_clean_with_no_residue(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            OB.run_reconfigure(d, add=["obsidian"], assume_yes=True,
                               detector=_detector({"obsidian"}))
            self.assertIn("obsidian", _chain(d, "memory_store"))
            res = OB.run_reconfigure(d, remove=["obsidian"], confirm=_yes, out=lambda _: None,
                                     detector=_detector({"obsidian"}))
            self.assertTrue(res.changed)
            self.assertIn("obsidian", res.removed)
            # gone from the capability chain AND from the tools table — no residue (K6).
            self.assertNotIn("obsidian", _chain(d, "memory_store"))
            from mokata import config_cmd
            _f, tools = config_cmd.config_get(d, "tools")
            self.assertNotIn("obsidian", tools or {})

    def test_remove_round_trips_back_to_the_original_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            original = json.loads(_manifest_bytes(d))            # semantic, not byte, identity
            OB.run_reconfigure(d, add=["obsidian"], assume_yes=True,
                               detector=_detector({"obsidian"}))
            self.assertNotEqual(json.loads(_manifest_bytes(d)), original)
            OB.run_reconfigure(d, remove=["obsidian"], assume_yes=True,
                               detector=_detector({"obsidian"}))
            self.assertEqual(json.loads(_manifest_bytes(d)), original,
                             "add→remove left residue (manifest didn't return to its original)")


# ============================================================ switch a backend (config, gated)
class TestSwitchBackend(unittest.TestCase):
    def test_switch_backend_updates_config_on_approval(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            res = OB.run_reconfigure(d, config_edits={"tools.sqlite.config.path": "mem/custom.db"},
                                     confirm=_yes, out=lambda _: None, detector=_detector(set()))
            self.assertTrue(res.changed)
            from mokata import config_cmd
            found, val = config_cmd.config_get(d, "tools.sqlite.config.path")
            self.assertTrue(found)
            self.assertEqual(val, "mem/custom.db")

    def test_switch_backend_declined_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            before = _manifest_bytes(d)
            OB.run_reconfigure(d, config_edits={"tools.sqlite.config.path": "mem/custom.db"},
                               confirm=_no, out=lambda _: None, detector=_detector(set()))
            self.assertEqual(_manifest_bytes(d), before)

    def test_setting_a_value_already_in_place_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            OB.run_reconfigure(d, config_edits={"tools.sqlite.config.path": "mem/x.db"},
                               assume_yes=True, detector=_detector(set()))
            before = _manifest_bytes(d)
            res = OB.run_reconfigure(d, config_edits={"tools.sqlite.config.path": "mem/x.db"},
                                     assume_yes=True, detector=_detector(set()))
            self.assertFalse(res.changed)
            self.assertEqual(_manifest_bytes(d), before)


# ============================================================ re-detect / profile / absent
class TestRedetectProfileRecommend(unittest.TestCase):
    def test_redetect_picks_up_a_newly_present_tool(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            # first detection: postgres absent → recommended, not wired
            plan_absent = OB.plan_reconfigure(d, detector=_detector(set()), add=["postgres"])
            self.assertFalse(plan_absent.detected["postgres"])
            self.assertNotIn("postgres", plan_absent.added)
            self.assertTrue(any("postgres" in r for r in plan_absent.recommended))
            # later: postgres now installed → re-detect catches it, now wireable
            plan_present = OB.plan_reconfigure(d, detector=_detector({"postgres"}), add=["postgres"])
            self.assertTrue(plan_present.detected["postgres"])
            self.assertIn("postgres", plan_present.added)

    def test_change_profile(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d, profile="standard")
            res = OB.run_reconfigure(d, profile="full", confirm=_yes, out=lambda _: None,
                                     detector=_detector(set()))
            self.assertTrue(res.changed)
            self.assertEqual(Surface.load(d).manifest.profile, "full")

    def test_absent_tool_is_recommended_not_installed(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            before = _manifest_bytes(d)
            res = OB.run_reconfigure(d, add=["postgres"], assume_yes=True,
                                     detector=_detector(set()))
            self.assertNotIn("postgres", _chain(d, "memory_store"))     # never wired
            self.assertTrue(any("pip install" in r for r in res.recommended))
            self.assertEqual(_manifest_bytes(d), before, "an absent tool was silently wired")


# ============================================================ --yes + interactive Q&A
class TestModes(unittest.TestCase):
    def test_non_interactive_yes_path_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            res = OB.run_reconfigure(d, add=["postgres"], assume_yes=True, ask=_boom,
                                     confirm=_boom, out=lambda _: None,
                                     detector=_detector({"postgres"}))
            self.assertTrue(res.changed)
            self.assertIn("postgres", _chain(d, "memory_store"))

    def test_interactive_qanda_wires_on_approval(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)

            def ask(prompt, choices, default):
                return default                       # keep the current profile

            def confirm(prompt):
                if "postgres" in prompt:             # "Wire newly-available 'postgres'?"
                    return True
                if "obsidian" in prompt or "Keep" in prompt:
                    return True                      # keep anything already wired
                return "Apply" in prompt             # the final gate

            res = OB.run_reconfigure(d, ask=ask, confirm=confirm, out=lambda _: None,
                                     detector=_detector({"postgres"}))
            self.assertTrue(res.changed)
            self.assertIn("postgres", _chain(d, "memory_store"))


# ============================================================ degrade-clean
class TestDegradeClean(unittest.TestCase):
    def test_uninitialized_repo_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            res = OB.run_reconfigure(d, add=["postgres"], assume_yes=True,
                                     detector=_detector({"postgres"}), out=lambda _: None)
            self.assertFalse(res.initialized)
            self.assertTrue(res.aborted)
            self.assertFalse(Surface.is_initialized(d))


# ============================================================ surfaces (CLI / MCP / parity)
class TestCliSurface(unittest.TestCase):
    def _run(self, argv):
        from mokata import cli
        out, err = io.StringIO(), io.StringIO()
        so, se = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = cli.main(argv)
        finally:
            sys.stdout, sys.stderr = so, se
        return rc, out.getvalue(), err.getvalue()

    def test_cli_reconfigure_changes_profile(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d, profile="standard")
            rc, out, err = self._run(["reconfigure", "--profile", "full", "--yes", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertEqual(Surface.load(d).manifest.profile, "full")

    def test_cli_reconfigure_noop_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            rc, out, err = self._run(["reconfigure", "--yes", "--path", d])
            self.assertEqual(rc, 0, err)
            self.assertIn("no changes", (out + err).lower())


class TestMcpSurface(unittest.TestCase):
    def test_reconfigure_is_a_gated_write_tool(self):
        from mokata import mcp_server as M
        self.assertIn("reconfigure", M.write_tool_names())

    def test_propose_only_without_approval(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _init(d)
            before = _manifest_bytes(d)
            res = M.reconfigure(path=d, profile="full")
            self.assertEqual(res["status"], "proposed")
            self.assertEqual(_manifest_bytes(d), before, "a proposed reconfigure wrote the manifest")

    def test_approve_applies(self):
        from mokata import mcp_server as M
        with tempfile.TemporaryDirectory() as d:
            _init(d, profile="standard")
            res = M.reconfigure(path=d, profile="full", approve=True)
            self.assertTrue(res["committed"])
            self.assertEqual(Surface.load(d).manifest.profile, "full")


class TestParityAndTemplate(unittest.TestCase):
    def test_reconfigure_in_matrix_and_parity_passes(self):
        from mokata import parity
        self.assertIn("reconfigure", parity.SURFACE_MATRIX)
        s = parity.SURFACE_MATRIX["reconfigure"]
        self.assertIn("reconfigure", s.slash)
        self.assertIn("reconfigure", s.mcp_write)
        self.assertTrue(parity.verify_parity().ok, parity.verify_parity().render())

    def test_slash_template_exists_and_is_namespaced(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "templates", "commands", "reconfigure.md")
        self.assertTrue(os.path.exists(path), "reconfigure.md missing")
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
        self.assertIn("name: reconfigure", md)
        self.assertIn("description: mokata ·", md)


if __name__ == "__main__":
    unittest.main()
