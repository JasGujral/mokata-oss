"""RT.S2 A1 — /mokata:menu: the one-screen command palette.

The palette lists EVERY shipped /mokata: command and every bundled skill, DERIVED from their
actual files (templates/commands/*.md + skills/*/SKILL.md) — single-source, so adding/removing a
file changes the menu with no code edit (the Stage 57 no-drift lesson). Read-only; renders through
the A4 legibility box/table + colour gate (plain ASCII when piped / NO_COLOR / ascii_only).
"""

import io
import os
import re
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli, menu
from mokata.harness_setup import plan_setup

ROOT = os.path.join(os.path.dirname(__file__), "..")
CMDS_DIR = os.path.join(ROOT, "src", "mokata", "templates", "commands")
SKILLS_DIR = os.path.join(ROOT, "src", "mokata", "skills")

ESC = "\x1b["


def _command_stems():
    return sorted(os.path.splitext(f)[0] for f in os.listdir(CMDS_DIR) if f.endswith(".md"))


def _skill_names():
    return sorted(d for d in os.listdir(SKILLS_DIR)
                  if os.path.isfile(os.path.join(SKILLS_DIR, d, "SKILL.md")))


def _has_gate(path):
    with open(path, encoding="utf-8") as fh:
        return re.search(r"(?m)^## Gate", fh.read()) is not None


# ============================================================= single-source enumeration (no drift)
class TestSingleSourceEnumeration(unittest.TestCase):
    def test_lists_every_command_template(self):
        names = sorted(e.name for e in menu.list_commands())
        self.assertEqual(names, _command_stems())          # parity with the dir — can't drift

    def test_lists_every_bundled_skill(self):
        names = sorted(e.name for e in menu.list_skills())
        self.assertEqual(names, _skill_names())

    def test_command_count_matches_the_directory(self):
        self.assertEqual(len(menu.list_commands()), len(_command_stems()))

    def test_every_entry_carries_a_description_from_frontmatter(self):
        for e in menu.list_commands() + menu.list_skills():
            self.assertTrue(e.description, f"{e.name} has no frontmatter description")


# ============================================================= gate marker accuracy
class TestGateMarker(unittest.TestCase):
    def test_gated_commands_are_flagged_and_ungated_are_not(self):
        expected = {os.path.splitext(f)[0]: _has_gate(os.path.join(CMDS_DIR, f))
                    for f in os.listdir(CMDS_DIR) if f.endswith(".md")}
        got = {e.name: e.gated for e in menu.list_commands()}
        self.assertEqual(got, expected)

    def test_at_least_one_gated_and_one_ungated_exist(self):
        gated = {e.gated for e in menu.list_commands()}
        self.assertEqual(gated, {True, False})             # the marker distinguishes, both present

    def test_gate_marker_shows_for_gated_rows_only(self):
        out = menu.render_menu(ascii_only=True, color=False)
        # a known gated command (spec) and a known ungated one (tour) render differently
        self.assertTrue(any("spec" in ln and "[gate]" in ln for ln in out.splitlines()))
        self.assertTrue(any(ln.strip().startswith("| tour") and "[gate]" not in ln
                            for ln in out.splitlines()))


# ============================================================= degrade-clean rendering
class TestRendering(unittest.TestCase):
    def test_ascii_mode_has_zero_escape_codes(self):
        self.assertNotIn(ESC, menu.render_menu(ascii_only=True, color=False))

    def test_no_color_env_forces_plain_ascii(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            out = menu.render_menu()                       # auto-detect -> colour off
        self.assertNotIn(ESC, out)
        for ch in "┌┐└┘─│┼┬┴├┤":
            self.assertNotIn(ch, out)                      # ascii box fallback too

    def test_colour_renders_only_on_a_forced_tty_without_no_color(self):
        env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
        with mock.patch.dict(os.environ, env, clear=True):
            out = menu.render_menu(color=True, ascii_only=False)
        self.assertIn(ESC, out)                            # green gate marks when coloured

    def test_render_routes_through_the_legibility_helpers(self):
        out = menu.render_menu(ascii_only=False, color=False)
        self.assertIn("│", out)                            # legibility.table column separator
        self.assertIn("┌", out)                            # legibility.box border


# ============================================================= CLI wiring + piped output
class TestCliMenu(unittest.TestCase):
    def test_menu_subcommand_is_registered(self):
        parser = cli.build_parser()
        self.assertIn("menu", cli._subcommand_set(parser))

    def test_menu_runs_and_lists_commands_and_skills(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = cli.main(["menu"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        # every command + skill name appears in the printed palette
        for name in _command_stems():
            self.assertIn(name, out)
        for name in _skill_names():
            self.assertIn(name, out)

    def test_piped_menu_output_has_no_escape_codes(self):
        # a captured (non-TTY) stdout must be plain ASCII — no ANSI leaks into a pipe
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            cli.main(["menu"])
        self.assertNotIn(ESC, buf.getvalue())


# ============================================================= the menu.md command template
class TestMenuTemplate(unittest.TestCase):
    def test_menu_template_exists_with_valid_frontmatter(self):
        path = os.path.join(CMDS_DIR, "menu.md")
        self.assertTrue(os.path.isfile(path), "templates/commands/menu.md is missing")
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
        from mokata.agent_skills import parse_frontmatter
        fm = parse_frontmatter(md)
        self.assertEqual(fm.get("name"), "menu")
        self.assertTrue(fm.get("description"), "menu.md has no frontmatter description")

    def test_menu_template_is_read_only_no_gate(self):
        with open(os.path.join(CMDS_DIR, "menu.md"), encoding="utf-8") as fh:
            self.assertIsNone(re.search(r"(?m)^## Gate", fh.read()))

    def test_menu_ships_through_the_setup_command_set(self):
        # single-source: setup globs templates/commands/*.md, so menu rides the same path
        plan = plan_setup("claude")
        self.assertIn("menu.md", plan.command_files)


# ============================================================= read-only (no state writes)
class TestReadOnly(unittest.TestCase):
    def test_render_is_deterministic(self):
        self.assertEqual(menu.render_menu(ascii_only=True, color=False),
                         menu.render_menu(ascii_only=True, color=False))


if __name__ == "__main__":
    unittest.main()
