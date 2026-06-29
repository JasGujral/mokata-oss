"""Stage 52c — docs-vs-code consistency guard, so the reference can't drift again.

Every `mokata <subcommand>` registered in the argparse parser MUST have a `### \`mokata
<cmd> …\`` entry in reference/cli.md, and every shipped `/mokata:*` command template MUST be
mentioned somewhere in the user docs. The test FAILS (naming the offenders) on an undocumented
command — the audit's findings, frozen as a regression.
"""

import os
import re
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.cli import build_parser

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _subcommands():
    parser = build_parser()
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            return set(action.choices)
    return set()


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _user_docs_text():
    """All user-facing docs concatenated (excludes the internal docs/build/ planning tree)."""
    parts = []
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "docs")):
        if os.sep + "build" in dirpath:
            continue
        for name in files:
            if name.endswith(".md"):
                with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                    parts.append(fh.read())
    return "\n".join(parts)


class TestDocsConsistency(unittest.TestCase):
    def test_every_cli_subcommand_has_a_cli_reference_entry(self):
        subs = _subcommands()
        self.assertTrue(subs, "no subcommands parsed — parser shape changed?")
        cli = _read("docs/reference/cli.md")
        documented = set(re.findall(r"###\s+`mokata\s+([a-z][a-z-]*)", cli))
        missing = sorted(subs - documented)
        self.assertEqual(
            missing, [],
            f"CLI subcommands missing a `### `mokata <cmd>`` entry in reference/cli.md: "
            f"{missing}")

    def test_no_stale_cli_reference_entries(self):
        # Every documented `### `mokata X`` header must be a real registered subcommand.
        cli = _read("docs/reference/cli.md")
        documented = set(re.findall(r"###\s+`mokata\s+([a-z][a-z-]*)", cli))
        subs = _subcommands()
        stale = sorted(documented - subs)
        self.assertEqual(stale, [],
                         f"reference/cli.md documents commands that don't exist: {stale}")

    def test_every_slash_command_template_is_mentioned_in_docs(self):
        cmds_dir = os.path.join(ROOT, "templates", "commands")
        stems = sorted(os.path.splitext(f)[0]
                       for f in os.listdir(cmds_dir) if f.endswith(".md"))
        self.assertTrue(stems, "no command templates found?")
        docs = _user_docs_text()
        missing = [s for s in stems
                   if f"/mokata:{s}" not in docs and f"mokata {s}" not in docs]
        self.assertEqual(missing, [],
                         f"/mokata:* commands with no mention in the user docs: {missing}")


if __name__ == "__main__":
    unittest.main()
