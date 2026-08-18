"""Stage 45 — repo / OSS hardening: the new workflows + config are present, valid, and
carry the intended (least-privilege) shape. YAML is PARSED, and PyYAML is a required test
dependency (requirements/ci.txt) — its absence raises, it does not skip."""

import glob
import os
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)
from _workflow_pins import safe_load

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


class TestRepoHardening(unittest.TestCase):
    NEW_FILES = (
        ".github/dependabot.yml",
        ".github/workflows/codeql.yml",
        ".github/workflows/scorecard.yml",
        ".github/CODEOWNERS",
    )

    def test_new_files_exist(self):
        for rel in self.NEW_FILES:
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), f"missing {rel}")

    def test_all_github_yaml_parses(self):
        # This test IS the parse. Skipping it when the parser is absent reported "every .github
        # YAML is valid" having read none of them (PYYAML-SKIP-CLUSTER); `safe_load` raises.
        # CORPUS: THE WORKING TREE. These files are shipped assets — `rsync` copies whatever is on
        # disk, so a stray untracked one is published and belongs in this check. An extra file makes
        # the assertion STRICTER, never more likely to pass, so the walk cannot hide a violation.
        files = set(glob.glob(os.path.join(ROOT, ".github/**/*.yml"), recursive=True))
        files.add(os.path.join(ROOT, ".github/dependabot.yml"))
        for path in sorted(files):
            with self.subTest(file=path):
                with open(path, encoding="utf-8") as fh:
                    safe_load(fh.read(), "verify every .github YAML file parses")

    def test_dependabot_is_github_actions_weekly(self):
        text = _read(".github/dependabot.yml")
        self.assertIn("github-actions", text)
        self.assertIn("weekly", text)

    def test_codeql_python_triggers_and_least_privilege(self):
        text = _read(".github/workflows/codeql.yml")
        self.assertIn("languages: python", text)
        self.assertIn("security-events: write", text)   # only the alert scope it needs
        self.assertIn("contents: read", text)
        for trigger in ("push:", "pull_request:", "schedule:"):
            self.assertIn(trigger, text)

    def test_codeowners_has_a_default_owner(self):
        text = _read(".github/CODEOWNERS")
        self.assertIn("@JasGujral", text)
        self.assertTrue(any(line.strip().startswith("*")
                            for line in text.splitlines()))

    def test_scorecard_scoped_to_public_repo(self):
        # Skipped on the private dev mirror so it's a no-op there; runs on mokata-oss.
        self.assertIn("github.repository == 'JasGujral/mokata-oss'",
                      _read(".github/workflows/scorecard.yml"))


if __name__ == "__main__":
    unittest.main()
