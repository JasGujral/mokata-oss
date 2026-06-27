"""L1 — the shipped slash-command templates are rendered from the single skill-registry
source, so prompt and gate can't silently drift from what the CLI runs."""

import os
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.skills import command_markdown, get_skill

# brainstorm.md predates this stage (Stage 3) and keeps its own format.
GENERATED = ("spec", "test", "develop", "review", "debug", "optimize", "bug", "onboard")


class TestCommandTemplates(unittest.TestCase):
    def _path(self, name):
        here = os.path.dirname(__file__)
        return os.path.join(here, "..", "templates", "commands", f"{name}.md")

    def test_each_command_template_exists_and_matches_source(self):
        for name in GENERATED:
            with self.subTest(command=name):
                path = self._path(name)
                self.assertTrue(os.path.exists(path), f"{name}.md missing")
                with open(path, encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), command_markdown(get_skill(name)))

    def test_template_carries_gate_and_standalone_note(self):
        with open(self._path("test"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Gate", text)
        self.assertIn("standalone", text.lower())


if __name__ == "__main__":
    unittest.main()
