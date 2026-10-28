"""0.0.26 L3 — CC-COMPAT (doc 84): the Claude Code surface manifest, the floor derived from it, the
doctor that names a missing surface, and docs that cannot drift from the manifest.

The live half (the latest `claude` validating mokata's generated files) is
`test_l3_cc_contract_live.py`, run weekly by `.github/workflows/cc-compat.yml`.
"""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _internal_scope
import _support

from mokata import claude_code_version as CC
from mokata import harness_setup as HS
from mokata.config import Surface
from mokata.govern import doctor as D
from mokata.init import init_repo

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "mokata"


class TheManifestIsWellFormed(unittest.TestCase):

    def test_every_surface_is_complete_and_unique(self):
        ids = [s["id"] for s in CC.MANIFEST["surfaces"]]
        self.assertEqual(len(ids), len(set(ids)), "duplicate surface ids")
        for s in CC.MANIFEST["surfaces"]:
            with self.subTest(s["id"]):
                for key in ("kind", "scope", "used_by", "loss", "source"):
                    self.assertTrue(s.get(key), key)
                self.assertIn(s["scope"], CC.SCOPES)
                if s["since"] is not None:
                    self.assertIsNotNone(CC.parse(s["since"]))

    def test_an_undated_surface_says_why_it_is_still_safe(self):
        undated = [s for s in CC.MANIFEST["surfaces"] if s["since"] is None]
        self.assertTrue(undated)
        for s in undated:
            # undated surfaces are only safe if the weekly job checks them, or losing them is benign
            self.assertTrue(s.get("expect") or "weekly" in s["source"] or "none:" in s["loss"],
                            s["id"])

    def test_the_manifest_records_where_it_was_checked(self):
        self.assertIn("changelog", CC.MANIFEST["sources"])
        self.assertIsNotNone(CC.parse(CC.MANIFEST["checked_against"]))

    def test_a_malformed_manifest_is_refused(self):
        bad = {"surfaces": [{"id": "x", "kind": "cli", "since": "two", "scope": "core"}]}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(bad, fh)
            with self.assertRaises(ValueError):
                CC._load_manifest(p)
            bad["surfaces"][0].update(since="1.0.0", scope="opt-in")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(bad, fh)
            with self.assertRaises(ValueError):     # an opt-in surface must name its flag
                CC._load_manifest(p)

    def test_the_wheel_ships_the_manifest(self):
        block = (ROOT / "pyproject.toml").read_text(encoding="utf-8").split(
            "[tool.setuptools.package-data]", 1)[1].split("\n[", 1)[0]
        self.assertIn('"claude_code_compat.json"', block)


class TheFloorIsDerived(unittest.TestCase):

    def test_the_floor_is_the_newest_dated_core_surface(self):
        core = [CC.parse(s["since"]) for s in CC.MANIFEST["surfaces"]
                if s["scope"] == "core" and s["since"]]
        self.assertEqual(max(core), CC.MIN_VERSION)

    def test_opt_in_surfaces_do_not_raise_the_floor(self):
        self.assertGreater(CC.since("hook.onFailure"), CC.MIN_VERSION)
        self.assertEqual(["hook.type.http", "hook.command.args"],
                         [s["id"] for s in CC.opt_in_surfaces("--ide-bridge")])

    def test_the_http_gates_floor_comes_from_the_manifest(self):
        self.assertEqual(CC.since("hook.onFailure"), HS.HTTP_GATES_MIN_CLAUDE_CODE)

    def test_the_floor_today(self):
        # P31 session worktrees (default on): resume returns a session to its worktree from 2.1.198.
        # Below that, agent_id on tool hooks (2.1.69) is what lets the gate tell a subagent apart.
        self.assertEqual((2, 1, 198), CC.MIN_VERSION)
        self.assertEqual(["behavior.worktree-session-resume"], CC.missing_features((2, 1, 197)))
        self.assertEqual(["field.tool-hook.agent_id", "behavior.worktree-session-resume"],
                         CC.missing_features((2, 1, 68)))


class EverySurfaceMokataWiresIsListed(unittest.TestCase):
    """Drift guards: a new hook event / CLI flag mokata starts using without a manifest row reds."""

    def _ids(self):
        return {s["id"] for s in CC.MANIFEST["surfaces"]}

    def test_every_hook_event_setup_wires(self):
        for event in HS.expected_hook_wiring():
            self.assertIn(f"hook.{event}", self._ids(), event)
        for event in HS.IDE_BRIDGE_EVENTS:
            self.assertIn(f"hook.{event}", self._ids(), event)
        self.assertIn("hook.PreToolUse", self._ids())     # http gates

    def test_every_hook_event_the_plugin_wires(self):
        hooks = json.loads((PKG / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        for event in hooks:
            self.assertIn(f"hook.{event}", self._ids(), event)

    def test_every_payload_field_the_hooks_depend_on(self):
        src = (PKG / "hook_cli.py").read_text(encoding="utf-8")
        for field, sid in (("agent_id", "field.tool-hook.agent_id"),
                           ("last_assistant_message", "field.last_assistant_message"),
                           ("source", "hook.SessionStart"), ("cwd", "field.cwd")):
            self.assertIn(f'payload.get("{field}")', src)
            self.assertIn(sid, self._ids())

    def test_every_claude_flag_mokata_spawns(self):
        # the `claude` argv mokata builds: `--version` (doctor) and `--resume` (IDE terminal)
        spawned = set()
        for py in PKG.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if 'which("claude")' in text:
                spawned |= set(re.findall(r'"(--[a-z-]+)"', text.split('which("claude")', 1)[1][:600]))
        self.assertEqual({"--version", "--resume"}, spawned)
        for flag in spawned:
            self.assertIn(f"cli.{flag}", self._ids())


class DocsAgreeWithTheManifest(unittest.TestCase):
    STATEMENT = re.compile(r"Claude Code (?:≥ ?|>= ?|v)?(\d+\.\d+\.\d+)(?:\+| or newer| or later)?")

    def _statements(self):
        # The SHIPPED docs only (O16): a dev tree's `docs/build/` and `docs/talks/` quote the
        # minimum of the release they were written for, on purpose — grading a history reds the
        # floor guard on its own records. `_internal_scope` derives the boundary from sync-public.
        files = [ROOT / "README.md"] + [p for p in (ROOT / "docs").rglob("*.md")
                                        if p.name != "changelog.md"
                                        and _internal_scope.is_shipped(str(ROOT), str(p))]
        for p in files:
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                for m in self.STATEMENT.finditer(line):
                    yield p.relative_to(ROOT).as_posix(), n, line, m.group(1)

    def test_readme_states_the_floor(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"Requires Claude Code ≥ {CC.fmt(CC.MIN_VERSION)}", readme)

    def test_every_minimum_stated_in_the_docs_is_the_manifests(self):
        opt_in = {s["since"]: s["opt_in"] for s in CC.MANIFEST["surfaces"] if s["scope"] == "opt-in"}
        found = list(self._statements())
        self.assertTrue(found)
        for rel, n, line, version in found:
            with self.subTest(f"{rel}:{n}"):
                if version in opt_in:          # an opt-in flag's own minimum, named with the flag
                    self.assertIn(opt_in[version].lstrip("-"), line + rel)
                    continue
                self.assertEqual(CC.fmt(CC.MIN_VERSION), version, line)

    def test_the_docs_check_fires_on_a_stale_statement(self):
        stale = "Requires Claude Code 2.1.0+ for the per-session features"
        [(_v)] = [m.group(1) for m in self.STATEMENT.finditer(stale)]
        self.assertNotEqual(CC.fmt(CC.MIN_VERSION), _v)


class TheDoctorNamesTheMissingSurface(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = self._t.name
        init_repo(root=self.root, profile="standard", assume_yes=True, out=lambda _: None)

    def tearDown(self):
        self._t.cleanup()

    def _codes(self, version, env=None):
        with mock.patch.object(CC, "installed_version", return_value=version), \
                mock.patch.dict(os.environ, env or {}, clear=False):
            for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
                if not env or k not in env:
                    os.environ.pop(k, None)
            return D.diagnose(Surface.load(self.root)).findings

    def test_below_the_floor_names_the_surface(self):
        [f] = [f for f in self._codes((2, 1, 50)) if f.code == "claude-code-too-old"]
        self.assertIn("field.tool-hook.agent_id", f.detail)
        self.assertIn("2.1.69", f.detail)

    def test_at_the_floor_is_silent(self):
        self.assertNotIn("claude-code-too-old", [f.code for f in self._codes(CC.MIN_VERSION)])

    def _wire_bridge(self, windows):
        hooks = HS.ide_bridge_hooks("http://127.0.0.1:5/api/bus", "tok", self.root, windows=windows)
        p = Path(self.root) / ".claude" / HS.LOCAL_SETTINGS
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
        return p

    def test_the_bridge_shapes_are_detected(self):
        self.assertEqual(["hook.type.http"], HS.ide_bridge_surfaces(self._wire_bridge(False)))
        self.assertEqual(["hook.command.args"], HS.ide_bridge_surfaces(self._wire_bridge(True)))

    def test_a_wired_bridge_on_a_too_old_claude_code_is_named(self):
        self._wire_bridge(True)
        s = Surface.load(self.root)
        [f] = D.ide_bridge_version_findings(s, version=(2, 1, 100))
        self.assertEqual("ide-bridge-claude-code-too-old", f.code)
        self.assertIn("hook.command.args", f.detail)
        self.assertIn("2.1.139", f.detail)
        self.assertEqual([], D.ide_bridge_version_findings(s, version=(2, 1, 139)))
        self._wire_bridge(False)
        [f] = D.ide_bridge_version_findings(s, version=(2, 1, 62))
        self.assertIn("hook.type.http", f.detail)

    def test_no_bridge_is_silent(self):
        self.assertEqual([], D.ide_bridge_version_findings(Surface.load(self.root), version=(1, 0, 0)))


if __name__ == "__main__":
    unittest.main()
