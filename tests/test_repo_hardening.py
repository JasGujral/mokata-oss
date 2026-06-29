"""Stage 45 — repo / OSS hardening: the new workflows + config are present, valid, and
carry the intended (least-privilege) shape. YAML is parsed when PyYAML is available
(not a mokata dependency); the structural checks run unconditionally either way."""

import glob
import os
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

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

    @unittest.skipUnless(_HAVE_YAML, "PyYAML not installed (not a mokata dependency)")
    def test_all_github_yaml_parses(self):
        files = set(glob.glob(os.path.join(ROOT, ".github/**/*.yml"), recursive=True))
        files.add(os.path.join(ROOT, ".github/dependabot.yml"))
        for path in sorted(files):
            with self.subTest(file=path):
                with open(path, encoding="utf-8") as fh:
                    yaml.safe_load(fh)            # raises on invalid YAML

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
