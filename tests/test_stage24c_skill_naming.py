"""Stage 24 Part C — skill naming & discoverability: every skill summary and every shipped
`/mokata:<name>` command description carries the `mokata ·` marker, so the `/` menu and
`mokata skills` output read consistently and it can't regress.
"""

import glob
import os
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.skills import command_markdown, get_skill, list_skills, skill_names

MARKER = "mokata ·"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS_DIR = os.path.join(ROOT, "templates", "commands")


class TestSkillSummariesPrefixed(unittest.TestCase):
    def test_every_skill_summary_carries_the_marker(self):
        for name in skill_names():
            with self.subTest(skill=name):
                self.assertTrue(
                    get_skill(name).summary.startswith(MARKER),
                    f"skill '{name}' summary must start with '{MARKER}'")

    def test_catalog_lines_carry_the_marker(self):
        for _name, summary in list_skills():
            self.assertTrue(summary.startswith(MARKER))

    def test_generated_command_markdown_carries_the_marker(self):
        # the single source -> the frontmatter description carries it too
        for name in skill_names():
            md = command_markdown(get_skill(name))
            self.assertIn(f"description: {MARKER}", md)


class TestCommandTemplatesPrefixed(unittest.TestCase):
    def _description(self, path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("description:"):
                    return line[len("description:"):].strip()
        return None

    def test_every_command_template_description_carries_the_marker(self):
        paths = sorted(glob.glob(os.path.join(COMMANDS_DIR, "*.md")))
        self.assertTrue(paths, "no command templates found")
        for path in paths:
            with self.subTest(command=os.path.basename(path)):
                desc = self._description(path)
                self.assertIsNotNone(desc, f"{path} has no description")
                self.assertTrue(
                    desc.startswith(MARKER),
                    f"{os.path.basename(path)} description must start with '{MARKER}'")


if __name__ == "__main__":
    unittest.main()
