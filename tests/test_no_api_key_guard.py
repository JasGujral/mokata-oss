"""NO-API-KEY-GUARD (0.0.23 D7, doc 84:1238, plan 111 exit criterion 5).

mokata's product never needs or reads an LLM API key — it runs on the user's own Claude login.
This suite sweeps `src/mokata/` statically and fails on any key read, SDK import, credential-store
read, headless `claude -p` spawn, API endpoint, or import of the eval harness. Every rule carries a
positive control, so a sweep that silently stopped matching would red here instead of reading green.
"""

import os
import unittest

import _support  # noqa: F401  (src/ on the path + provisioning refusal)

import _no_api_key_sweep as S

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PKG = os.path.join(ROOT, "src", "mokata")


class TestTheProductNeedsNoApiKey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.findings, cls.counts, cls.broken = S.sweep(PKG)

    def test_src_mokata_has_no_api_key_read(self):
        bad = S.violations(self.findings)
        self.assertEqual(bad, [], "mokata's product must never need an API key (Jas, hard rule):\n"
                         + "\n".join(f"  {f.rule} {f.path}:{f.line} {f.detail}" for f in bad))

    def test_the_sweep_is_not_vacuous(self):
        # A sweep that walked the wrong directory would report zero findings over zero files.
        self.assertGreater(self.counts["python"], 150, self.counts)
        self.assertGreater(self.counts["text"], 20, self.counts)

    def test_every_python_file_parsed(self):
        # An unparsable file is an UN-swept file — never a clean one.
        self.assertEqual(self.broken, [])

    def test_allow_list_is_empty_for_the_product(self):
        # The allow-list is an owner decision. Empty is the claim; a non-empty list must be argued.
        self.assertEqual(S.ALLOW, {})

    def test_no_allow_list_entry_is_stale(self):
        live = {f.key() for f in self.findings}
        self.assertEqual([k for k in S.ALLOW if k not in live], [])


class TestEveryRuleFires(unittest.TestCase):
    """Positive controls: each rule must catch the shape it names."""

    def _rules(self, src):
        return {f.rule for f in S.scan_python(src, "x.py")}

    def test_r1_sdk_imports(self):
        for src in ("import anthropic", "from anthropic import Anthropic", "import openai.types",
                    "from claude_agent_sdk import query", "import claude_code_sdk",
                    "import importlib\nimportlib.import_module('anthropic')",
                    "__import__('openai')"):
            self.assertIn("R1", self._rules(src), src)

    def test_r2_key_env_var_names_in_any_shape(self):
        for src in ("import os\nos.environ['ANTHROPIC_API_KEY']",
                    "import os\nos.getenv('OPENAI_API_KEY')",
                    "import os\nos.environ.get('GEMINI_API_KEY', '')",
                    "NAME = 'ANTHROPIC_AUTH_TOKEN'",
                    "NAME = 'CLAUDE_CODE_OAUTH_TOKEN'"):
            self.assertIn("R2", self._rules(src), src)

    def test_r2_does_not_fire_on_prose_or_redaction_patterns(self):
        for src in ('"""Never needs an ANTHROPIC_API_KEY — runs on your login."""',
                    "import re\nP = re.compile(r'(TOKEN|API[_-]?KEY|_KEY)')",
                    "X = 'CONTEXT_SETTINGS_KEY'"):
            self.assertNotIn("R2", self._rules(src), src)

    def test_r3_credential_store(self):
        for src in ("P = '~/.claude/.credentials.json'",
                    "S = 'Claude Code-credentials'",
                    "A = ['security', 'find-generic-password', '-s', 'x']"):
            self.assertIn("R3", self._rules(src), src)

    def test_r4_headless_spawn(self):
        for src in ("A = ['claude', '-p', 'hi']", "A = ('claude', '--print', 'x')",
                    "A = ['/usr/bin/claude.exe', '--output-format', 'json', '-p', 'x']",
                    "C = 'claude -p \"do it\"'"):
            self.assertIn("R4", self._rules(src), src)

    def test_r4_does_not_fire_on_other_tools_print_flags(self):
        # bash_lane.py classifies `node -p`; that is not a claude spawn.
        self.assertNotIn("R4", self._rules("T = {'node': ('-e', '--eval', '-p', '--print')}"))
        self.assertNotIn("R4", self._rules("A = ['claude', 'mcp', 'list']"))

    def test_r5_endpoints(self):
        self.assertIn("R5", self._rules("U = 'https://api.anthropic.com/v1/messages'"))
        self.assertIn("R5", self._rules("U = 'https://api.openai.com/v1/chat'"))

    def test_r6_product_never_imports_the_eval_harness(self):
        self.assertIn("R6", self._rules("import bench.g2"))
        self.assertIn("R6", self._rules("from bench.g2 import runner"))
        self.assertNotIn("R6", self._rules("from .bench import x"))   # a relative module is not it

    def test_text_sweep_reads_prompts_and_shell(self):
        rules = {f.rule for f in S.scan_text(
            "export ANTHROPIC_API_KEY=sk\nrun: claude -p 'x'\ncat ~/.claude/.credentials.json\n",
            "skills/x/SKILL.md")}
        self.assertEqual(rules, {"R2", "R3", "R4"})

    def test_a_planted_violation_reds_the_sweep(self):
        # End to end over a real tree: a fake package with one planted read must produce a finding.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pkg = os.path.join(tmp, "src", "mokata")
            os.makedirs(pkg)
            with open(os.path.join(pkg, "ok.py"), "w", encoding="utf-8") as fh:
                fh.write("X = 1\n")
            with open(os.path.join(pkg, "leak.py"), "w", encoding="utf-8") as fh:
                fh.write("import os\nKEY = os.environ.get('ANTHROPIC_API_KEY')\n")
            findings, counts, broken = S.sweep(pkg)
            self.assertEqual(counts["python"], 2)
            self.assertEqual([(f.rule, f.path) for f in S.violations(findings)],
                             [("R2", "src/mokata/leak.py")])


if __name__ == "__main__":
    unittest.main()
