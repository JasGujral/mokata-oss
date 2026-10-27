"""0.0.26 L3 — CC-COMPAT live contract: mokata's generated Claude Code files against a REAL `claude`.

Runs wherever `claude` is on PATH, with NO login and NO API key (an empty, throwaway config dir and
an environment scrubbed of every credential): only `--version`, `--help` and `claude plugin
validate --strict` are called, none of which authenticates. Skipped when `claude` is absent, unless
`MOKATA_CC_CONTRACT=require` (the weekly `.github/workflows/cc-compat.yml`, which installs the latest
Claude Code), where an absent `claude` is a failure, never a skip.

What `claude plugin validate --strict` checks (measured on 2.1.306): plugin.json / marketplace.json
schema; every hooks file's event names, hook `type`, and per-type fields (an http hook's
`onFailure` enum, unknown types); skill/command frontmatter. mokata's settings.json hooks are not a
plugin file, so the test lifts them into a synthesized plugin, so the same validator reads them.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _internal_scope
import _support  # noqa: F401  (puts src/ on the path)

from mokata import claude_code_version as CC
from mokata import harness_setup as HS

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "mokata"
REQUIRE = os.environ.get("MOKATA_CC_CONTRACT") == "require"
CLAUDE = shutil.which("claude")
TIMEOUT = 180
_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                   "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


def _silent(*_a, **_k):
    return None


@unittest.skipUnless(CLAUDE or REQUIRE,
                     "Claude Code is not installed (the weekly cc-compat job runs this)")
class _Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CLAUDE:      # only reached under MOKATA_CC_CONTRACT=require: absent is a failure
            raise AssertionError("MOKATA_CC_CONTRACT=require but `claude` is not on PATH")
        cls._home = tempfile.TemporaryDirectory()
        env = {k: v for k, v in os.environ.items() if k not in _CREDENTIAL_ENV}
        env.update(HOME=cls._home.name, USERPROFILE=cls._home.name,
                   CLAUDE_CONFIG_DIR=os.path.join(cls._home.name, ".claude"),
                   DISABLE_AUTOUPDATER="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
        cls.env = env

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_home"):
            cls._home.cleanup()

    def claude(self, *args):
        p = subprocess.run([CLAUDE, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=TIMEOUT, env=self.env,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout + p.stderr

    def validate(self, path):
        code, out = self.claude("plugin", "validate", str(path), "--json", "--strict")
        start = out.find("{")
        self.assertGreaterEqual(start, 0, out[:2000])
        report, _ = json.JSONDecoder().raw_decode(out[start:])
        problems = []
        for part in [report.get("manifest") or {}] + list(report.get("contents") or []):
            for kind in ("errors", "warnings"):
                for item in part.get(kind) or []:
                    problems.append(f"{part.get('file')}: {kind[:-1]} {item.get('path')}: "
                                    f"{item.get('message')}")
        return code, report, problems


class TheInstalledClaudeCodeIsNewEnough(_Live):

    def test_version_parses_and_meets_the_floor(self):
        code, out = self.claude("--version")
        self.assertEqual(0, code, out)
        v = CC.parse(out)
        self.assertIsNotNone(v, out)
        self.assertGreaterEqual(v, CC.MIN_VERSION, f"latest Claude Code {out!r} < floor")

    def test_every_cli_surface_mokata_uses_still_exists(self):
        rows = [s for s in CC.MANIFEST["surfaces"] if s["kind"] == "cli"]
        self.assertTrue(rows)
        for s in rows:
            with self.subTest(s["id"]):
                code, out = self.claude(*s["argv"])
                self.assertEqual(0, code, out[:2000])
                self.assertIn(s["expect"], out)


class ThePluginFilesValidate(_Live):
    """⚠ Validation runs against what SHIPS, staged into a temp root (O16).

    `claude plugin validate --strict` reports every file at the plugin root, and a DEV tree has
    `CLAUDE.md` there — which the mirror never receives (`scripts/sync-public.sh`). Validating
    the dev tree therefore produced a warning about a file users never get, so Jas's run was red
    on a non-defect while the bed's mirror clone was green. Staging keeps the check honest in
    both trees: on the mirror nothing is excluded and the stage is the tree itself."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._stage = tempfile.mkdtemp(prefix="mokata-shipped-")
        for base in (".claude-plugin", "skills", "commands", "agents", "hooks", "src", "docs",
                     "README.md", "LICENSE"):
            src = os.path.join(str(ROOT), base)
            if not os.path.exists(src):
                continue
            if os.path.isfile(src):
                if _internal_scope.is_shipped(str(ROOT), src):
                    shutil.copy2(src, os.path.join(cls._stage, base))
                continue
            shutil.copytree(
                src, os.path.join(cls._stage, base),
                ignore=lambda d, names: [n for n in names
                                         if not _internal_scope.is_shipped(
                                             str(ROOT), os.path.join(d, n))])
        cls.shipped = Path(cls._stage)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._stage, ignore_errors=True)
        super().tearDownClass()

    def test_the_shipped_plugin_manifest(self):
        code, report, problems = self.validate(self.shipped / ".claude-plugin" / "plugin.json")
        self.assertEqual([], problems)
        self.assertEqual(0, code)
        self.assertEqual("plugin", report["manifest"]["type"])

    def test_the_shipped_marketplace_manifest(self):
        code, report, problems = self.validate(self.shipped / ".claude-plugin" / "marketplace.json")
        self.assertEqual([], problems)
        self.assertEqual(0, code)
        self.assertEqual("marketplace", report["manifest"]["type"])


class TheGeneratedSettingsValidate(_Live):
    """Every hook shape mokata writes — the plugin's hooks.json, `mokata setup claude`'s
    settings.json, `--ide-bridge` (POSIX http + Windows exec form) and `--http-gates` — plus the
    shipped skills and commands, read by the real validator."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.base = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def _generated_hooks(self):
        repo, home = self.base / "repo", self.base / "home"
        repo.mkdir()
        home.mkdir()
        plan = HS.plan_setup("claude", root=str(repo), home=str(home), ide_bridge=True,
                             http_gates=True)
        HS.apply_setup(plan, assume_yes=True, out=_silent)
        merged = {}

        def add(hooks):
            for event, groups in hooks.items():
                merged.setdefault(event, []).extend(groups)
        add(json.loads((PKG / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"])
        settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
        local = json.loads((repo / ".claude" / HS.LOCAL_SETTINGS).read_text(encoding="utf-8"))
        add(settings["hooks"])
        add(local["hooks"])
        # both bridge shapes, whatever OS this runs on (the settings file holds only this OS's)
        add(HS.ide_bridge_hooks("http://127.0.0.1:5/api/bus", "t", str(repo), windows=True))
        add(HS.ide_bridge_hooks("http://127.0.0.1:5/api/bus", "t", str(repo), windows=False))
        self.assertIn("statusLine", settings)
        return merged

    def _plugin(self, hooks, name="probe"):
        d = self.base / name
        (d / ".claude-plugin").mkdir(parents=True)
        (d / "hooks").mkdir()
        shipped = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        manifest = {k: shipped[k] for k in ("name", "version", "description", "author",
                                            "mcpServers")}
        manifest["hooks"] = "./hooks/hooks.json"
        (d / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        (d / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
        return d

    def test_mokatas_hooks_skills_and_commands_validate_strict(self):
        hooks = self._generated_hooks()
        types = {h.get("type") for groups in hooks.values() for g in groups for h in g["hooks"]}
        self.assertEqual({"command", "http"}, types)
        d = self._plugin(hooks)
        shutil.copytree(PKG / "skills", d / "skills")
        shutil.copytree(PKG / "templates" / "commands", d / "commands")
        code, _report, problems = self.validate(d)
        self.assertEqual([], problems)
        self.assertEqual(0, code)

    def test_firing_control_the_validator_reads_hook_content(self):
        # If the validator ever stopped reading hook bodies, the test above would pass vacuously.
        d = self._plugin({"PreToolUse": [{"hooks": [{"type": "http", "url": "http://x",
                                                     "onFailure": "explode"}]}],
                          "NotAnEvent": [{"hooks": [{"type": "command", "command": "x"}]}]})
        (d / "skills" / "zz").mkdir(parents=True)
        (d / "skills" / "zz" / "SKILL.md").write_text("---\nname: zz\n---\nbody\n", encoding="utf-8")
        code, _report, problems = self.validate(d)
        self.assertNotEqual(0, code)
        self.assertTrue(any("onFailure" in p for p in problems), problems)
        self.assertTrue(any("NotAnEvent" in p for p in problems), problems)
        self.assertTrue(any("SKILL.md" in p for p in problems), problems)   # frontmatter is read


if __name__ == "__main__":
    unittest.main()
