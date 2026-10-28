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

    def test_allow_list_is_only_the_presence_warning(self):
        # The allow-list is an owner decision. 0.0.26 L4: exactly the runtime billing WARNING's two
        # variable names, in exactly one file — nothing else in the product may name a key.
        self.assertEqual(set(S.ALLOW), {("R2", "src/mokata/billing_env.py", "ANTHROPIC_API_KEY"),
                                        ("R2", "src/mokata/billing_env.py", "ANTHROPIC_AUTH_TOKEN")})

    def test_the_allowed_file_never_hands_the_value_out(self):
        # The allowance is for a presence check. Every read of the environment in billing_env.py is
        # `env.get(name)` wrapped in bool(...) — a returned, formatted or logged value would not be.
        import ast
        with open(os.path.join(PKG, "billing_env.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        gets = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "get"]
        self.assertEqual(1, len(gets))
        node = gets[0]
        while not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "bool"):
            node = parents[node]          # KeyError if the read escapes bool(...) — the red

    def test_the_planted_import_in_a_copy_of_the_real_tree_reds(self):
        # Firing control over the REAL package: copy src/mokata, plant `import anthropic` into one
        # real module, sweep the copy. The live tree's verdict must flip from clean to exactly that.
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pkg = os.path.join(tmp, "src", "mokata")
            shutil.copytree(PKG, pkg, ignore=shutil.ignore_patterns("__pycache__"))
            target = os.path.join(pkg, "claude_code_version.py")
            with open(target, encoding="utf-8") as fh:
                text = fh.read()
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text.replace("import json\n", "import json\nimport anthropic\n", 1))
            bad = S.violations(S.sweep(pkg)[0])
            self.assertEqual([("R1", "src/mokata/claude_code_version.py", "import anthropic")],
                             [(f.rule, f.path, f.detail) for f in bad])

    def test_no_allow_list_entry_is_stale(self):
        live = {f.key() for f in self.findings}
        self.assertEqual([k for k in S.ALLOW if k not in live], [])


class TestTheEvalHarnessIsTheOnlyKeyReader(unittest.TestCase):
    """bench/ (outside the wheel) is documented as the one place a key may be read: the G2 judge's
    dedicated `MOKATA_G2_JUDGE_API_KEY` (LoCoMo reuses that client). Verified, not trusted: any
    other key name, endpoint or SDK import in bench/'s Python reds here."""

    def test_bench_reads_only_the_judge_key_and_scrubs_the_rest(self):
        findings, counts, broken = S.sweep(os.path.join(ROOT, "bench"))
        self.assertEqual([], broken)
        self.assertGreater(counts["python"], 10)
        py = sorted({(f.rule, os.path.basename(f.path), f.detail) for f in findings
                     if f.path.endswith(".py") and f.rule in ("R1", "R2", "R5")})
        self.assertEqual([
            ("R2", "agent.py", "ANTHROPIC_API_KEY"), ("R2", "agent.py", "ANTHROPIC_AUTH_TOKEN"),
            ("R2", "agent.py", "MOKATA_G2_JUDGE_API_KEY"), ("R2", "agent.py", "OPENAI_API_KEY"),
            ("R2", "judge.py", "MOKATA_G2_JUDGE_API_KEY"), ("R5", "judge.py", "api.anthropic.com"),
        ], py)
        # the four names in agent.py are its SCRUB list (removed from the child's env), not reads
        import sys
        sys.path.insert(0, ROOT)
        try:
            from bench.g2 import agent, judge
        finally:
            sys.path.remove(ROOT)
        self.assertTrue({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY",
                         "MOKATA_G2_JUDGE_API_KEY"} <= set(agent.SCRUBBED_ENV))
        self.assertEqual("MOKATA_G2_JUDGE_API_KEY", judge.JUDGE_KEY_ENV)


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
                    "C = 'claude -p \"do it\"'",
                    # 0.0.26 L4: --bare never reads the login (OAuth/keychain): a key-only spawn
                    "A = ['claude', '--bare']", "C = 'claude --bare --settings s.json'"):
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
