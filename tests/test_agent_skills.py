"""Agent Skills surface — mokata's capabilities appear in (and auto-trigger from) Claude
Code's Agent Skills list, as the model-invocable twin of the slash commands.

Guards:
  * the plugin manifest DECLARES a skills surface;
  * every shipped `skills/<name>/SKILL.md` has valid `name` + `description` frontmatter;
  * DRIFT: each shipped SKILL.md is exactly what the builder renders from its command
    template (one source — the two surfaces can't diverge);
  * `setup claude` WRITES `.claude/skills/<name>/SKILL.md` and `unsetup` removes them cleanly
    (no residue, never touching a user's own skills);
  * non-claude harnesses get NO skills surface (degrade-clean).
"""

import json
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.agent_skills import (
    CURATED_SKILLS,
    SKILL_MARKER,
    generate_skill_files,
    parse_frontmatter,
    skill_markdown,
)
from mokata.harness_setup import (
    apply_setup,
    apply_unsetup,
    plan_setup,
    plan_unsetup,
    resolve_targets,
    setup_harness,
)

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SKILLS_DIR = os.path.join(_REPO, "skills")
_TEMPLATES = os.path.join(_REPO, "templates", "commands")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestPluginDeclaresSkills(unittest.TestCase):
    def test_plugin_json_declares_skills_surface(self):
        data = json.loads(_read(os.path.join(_REPO, ".claude-plugin", "plugin.json")))
        self.assertIn("skills", data, "plugin.json must declare a `skills` surface")
        # string or array of paths, all pointing at real directories under the plugin root
        decl = data["skills"]
        paths = [decl] if isinstance(decl, str) else decl
        self.assertTrue(paths, "`skills` must not be empty")
        for rel in paths:
            self.assertTrue(os.path.isdir(os.path.join(_REPO, rel)),
                            f"declared skills path {rel} is not a directory")

    def test_commands_surface_kept_too(self):
        # both surfaces coexist — declaring skills must not drop the commands surface
        data = json.loads(_read(os.path.join(_REPO, ".claude-plugin", "plugin.json")))
        self.assertIn("commands", data)


class TestShippedSkillFiles(unittest.TestCase):
    def test_one_dir_per_curated_skill(self):
        for name in CURATED_SKILLS:
            path = os.path.join(_SKILLS_DIR, name, "SKILL.md")
            self.assertTrue(os.path.isfile(path), f"missing shipped skill {name}/SKILL.md")

    def test_no_stray_skill_dirs(self):
        # every dir under skills/ maps to a curated skill (no orphans left behind)
        present = {d for d in os.listdir(_SKILLS_DIR)
                   if os.path.isdir(os.path.join(_SKILLS_DIR, d))}
        self.assertEqual(present, set(CURATED_SKILLS))

    def test_each_skill_has_valid_frontmatter(self):
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                text = _read(os.path.join(_SKILLS_DIR, name, "SKILL.md"))
                fm = parse_frontmatter(text)
                self.assertEqual(fm.get("name"), name, "name must be the bare skill name")
                self.assertTrue(fm.get("description"), "a description is required to trigger")
                self.assertIn(SKILL_MARKER, text, "banner marker must be present")

    def test_when_to_use_carried_where_the_template_has_it(self):
        # brainstorm + onboard carry a `when_to_use` trigger; it must survive into the skill
        for name in ("brainstorm", "onboard"):
            with self.subTest(skill=name):
                fm = parse_frontmatter(_read(os.path.join(_SKILLS_DIR, name, "SKILL.md")))
                self.assertTrue(fm.get("when_to_use"), f"{name} should carry when_to_use")


class TestDriftGuard(unittest.TestCase):
    """The single-source guarantee: a shipped SKILL.md is byte-identical to what the builder
    renders from its command template. Regenerate skills/ if this fails — never hand-edit."""

    def test_shipped_skill_matches_generated(self):
        from pathlib import Path
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                shipped = _read(os.path.join(_SKILLS_DIR, name, "SKILL.md"))
                built = skill_markdown(name, Path(_TEMPLATES))
                self.assertEqual(shipped, built,
                                 f"{name}/SKILL.md drifted from its command template")

    def test_curated_skills_have_source_templates(self):
        for name in CURATED_SKILLS:
            self.assertTrue(os.path.isfile(os.path.join(_TEMPLATES, f"{name}.md")),
                            f"curated skill {name} has no command template")

    def test_body_is_the_command_protocol_verbatim(self):
        # the skill body must contain the command template's protocol body (no logic copy —
        # it IS the template body), so the two surfaces run the same protocol
        for name in ("brainstorm", "spec"):
            with self.subTest(skill=name):
                tmpl = _read(os.path.join(_TEMPLATES, f"{name}.md"))
                body = tmpl.split("---", 2)[2].lstrip("\n")
                self.assertIn(body, _read(os.path.join(_SKILLS_DIR, name, "SKILL.md")))


class TestSetupWiresSkills(unittest.TestCase):
    def test_setup_claude_writes_skill_files(self):
        with tempfile.TemporaryDirectory() as d:
            setup_harness("claude", root=d, scope="project", profile="standard",
                          assume_yes=True, out=lambda _: None)
            for name in CURATED_SKILLS:
                self.assertTrue(
                    os.path.isfile(os.path.join(d, ".claude", "skills", name, "SKILL.md")),
                    f"setup did not write skill {name}")

    def test_plan_lists_skills_and_render_mentions_them(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata.harness_setup import render_setup_plan
            plan = plan_setup("claude", root=d, scope="project")
            self.assertEqual(list(plan.skill_names), list(CURATED_SKILLS))
            rendered = render_setup_plan(plan)
            self.assertIn("Agent Skills", rendered)

    def test_unsetup_removes_only_mokata_skills(self):
        with tempfile.TemporaryDirectory() as d:
            setup_harness("claude", root=d, scope="project", profile="standard",
                          assume_yes=True, out=lambda _: None)
            skills_dir = os.path.join(d, ".claude", "skills")
            # a user's own skill must survive unsetup
            own = os.path.join(skills_dir, "my-own", "SKILL.md")
            os.makedirs(os.path.dirname(own), exist_ok=True)
            with open(own, "w", encoding="utf-8") as fh:
                fh.write("---\nname: my-own\ndescription: mine\n---\n\nnot mokata\n")

            plan = plan_unsetup("claude", root=d, scope="project")
            self.assertTrue(plan.has_skills)
            apply_unsetup(plan)

            for name in CURATED_SKILLS:
                self.assertFalse(
                    os.path.exists(os.path.join(skills_dir, name)),
                    f"unsetup left residue for {name}")
            self.assertTrue(os.path.isfile(own), "unsetup removed a user's own skill")

    def test_unsetup_removes_empty_skills_dir(self):
        with tempfile.TemporaryDirectory() as d:
            setup_harness("claude", root=d, scope="project", profile="standard",
                          assume_yes=True, out=lambda _: None)
            apply_unsetup(plan_unsetup("claude", root=d, scope="project"))
            # nothing else was in skills/, so it should be gone entirely (no residue)
            self.assertFalse(os.path.exists(os.path.join(d, ".claude", "skills")))


class TestNonClaudeDegradesClean(unittest.TestCase):
    def test_non_claude_harness_gets_no_skills_surface(self):
        # Agent Skills are a Claude Code feature; other harnesses wire nothing skill-related
        for harness in ("codex", "cursor", "gemini", "aider"):
            with self.subTest(harness=harness):
                t = resolve_targets("project", ".", harness=harness)
                self.assertIsNone(t.skills_dir)

    def test_codex_setup_writes_no_skill_files(self):
        with tempfile.TemporaryDirectory() as d:
            plan = plan_setup("codex", root=d, scope="project")
            self.assertEqual(list(plan.skill_names), [])
            apply_setup(plan, assume_yes=True, out=lambda _: None)
            # no `.claude/skills` under a codex setup
            self.assertFalse(os.path.exists(os.path.join(d, ".claude", "skills")))


class TestGeneratorParity(unittest.TestCase):
    def test_generate_and_ship_agree(self):
        from pathlib import Path
        files = generate_skill_files(Path(_TEMPLATES))
        self.assertEqual(set(files), set(CURATED_SKILLS))
        for name, content in files.items():
            self.assertEqual(content, _read(os.path.join(_SKILLS_DIR, name, "SKILL.md")))


if __name__ == "__main__":
    unittest.main()
