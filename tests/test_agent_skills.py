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
    curated_catalog,
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
_SKILLS_DIR = os.path.join(_REPO, "src", "mokata", "skills")
_TEMPLATES = os.path.join(_REPO, "src", "mokata", "templates", "commands")


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
        # every dir under skills/ maps to a curated pipeline skill OR a shipped domain skill
        # (DK.S1+ hand-authored) — no orphans left behind.
        from mokata.agent_skills import installed_skill_names
        present = {d for d in os.listdir(_SKILLS_DIR)
                   if os.path.isdir(os.path.join(_SKILLS_DIR, d))}
        self.assertEqual(present, set(installed_skill_names()))

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
        # it IS the template body), so the two surfaces run the same protocol. SK.S2: the body
        # carries the single-source grounding MARKER, which render expands to the canonical
        # block — so compare the EXPANDED body (the marker is the only rendered-time transform).
        from mokata.skills import expand_grounding
        for name in ("brainstorm", "spec"):
            with self.subTest(skill=name):
                tmpl = _read(os.path.join(_TEMPLATES, f"{name}.md"))
                body = expand_grounding(tmpl.split("---", 2)[2].lstrip("\n").rstrip())
                self.assertIn(body, _read(os.path.join(_SKILLS_DIR, name, "SKILL.md")))


class TestSetupWiresSkills(unittest.TestCase):
    # home=d is an isolated HOME (harmless leftover from the old plugin-dedup era). Setup now
    # ALWAYS writes the skills regardless of any installed plugin — see TestPluginCoexistReplace.
    def test_setup_claude_writes_skill_files(self):
        with tempfile.TemporaryDirectory() as d:
            setup_harness("claude", root=d, scope="project", profile="standard",
                          assume_yes=True, home=d, out=lambda _: None)
            for name in CURATED_SKILLS:
                self.assertTrue(
                    os.path.isfile(os.path.join(d, ".claude", "skills", name, "SKILL.md")),
                    f"setup did not write skill {name}")

    def test_plan_lists_skills_and_render_mentions_them(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata.harness_setup import render_setup_plan
            plan = plan_setup("claude", root=d, scope="project", home=d)
            self.assertEqual(list(plan.skill_names), list(CURATED_SKILLS))
            rendered = render_setup_plan(plan)
            self.assertIn("Agent Skills", rendered)

    def test_unsetup_removes_only_mokata_skills(self):
        with tempfile.TemporaryDirectory() as d:
            setup_harness("claude", root=d, scope="project", profile="standard",
                          assume_yes=True, home=d, out=lambda _: None)
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
                          assume_yes=True, home=d, out=lambda _: None)
            apply_unsetup(plan_unsetup("claude", root=d, scope="project"))
            # nothing else was in skills/, so it should be gone entirely (no residue)
            self.assertFalse(os.path.exists(os.path.join(d, ".claude", "skills")))


class TestPluginCoexistReplace(unittest.TestCase):
    """Stage 5 (design change): mokata setup is a REPLACE, not a dedup. It ALWAYS (over)writes the
    current curated skills into the project — the project copy is authoritative — regardless of
    whether the mokata PLUGIN is also installed. A reinstall thus always delivers the current,
    non-stale set (reliably-current beats deduped-but-stale). The old plugin-suppression is gone."""

    def _make_home_with_plugin(self, home, named="mokata"):
        # a realistic plugin layout: ~/.claude/plugins/<anything>/.claude-plugin/plugin.json
        pj = os.path.join(home, ".claude", "plugins", "repos", "acme",
                          "mokata", ".claude-plugin", "plugin.json")
        os.makedirs(os.path.dirname(pj), exist_ok=True)
        with open(pj, "w", encoding="utf-8") as fh:
            fh.write('{"name": "%s", "version": "0.0.7"}' % named)

    def test_plugin_present_still_writes_skills(self):
        # plugin installed → we STILL write the project skills (no suppression); plan says so.
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as d:
            self._make_home_with_plugin(home)
            plan = plan_setup("claude", root=d, scope="project", home=home)
            self.assertEqual(list(plan.skill_names), list(CURATED_SKILLS),
                             "skills must be written even when the plugin is present (replace)")
            from mokata.harness_setup import render_setup_plan
            render = render_setup_plan(plan)
            self.assertIn("Will write", render)
            self.assertNotIn("SKIPPED", render)

    def test_setup_writes_skill_files_even_when_plugin_present(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as d:
            self._make_home_with_plugin(home)
            setup_harness("claude", root=d, scope="project", profile="standard",
                          assume_yes=True, home=home, out=lambda _: None)
            for name in CURATED_SKILLS:
                self.assertTrue(
                    os.path.isfile(os.path.join(d, ".claude", "skills", name, "SKILL.md")),
                    f"project skill '{name}' must be written even with the plugin present")

    def test_no_plugin_writes_skills(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as d:
            self._make_home_with_plugin(home, named="somethingelse")
            plan = plan_setup("claude", root=d, scope="project", home=home)
            self.assertEqual(list(plan.skill_names), list(CURATED_SKILLS))

    def test_existing_project_skills_are_refreshed(self):
        # A project that ALREADY carries mokata skill copies must be REFRESHED (overwritten) on
        # re-setup — the whole point of replace: a reinstall never leaves stale content behind.
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as d:
            self._make_home_with_plugin(home)
            stale = os.path.join(d, ".claude", "skills", CURATED_SKILLS[0], "SKILL.md")
            os.makedirs(os.path.dirname(stale), exist_ok=True)
            with open(stale, "w", encoding="utf-8") as fh:
                fh.write("STALE OLD CONTENT")
            plan = plan_setup("claude", root=d, scope="project", home=home)
            self.assertEqual(list(plan.skill_names), list(CURATED_SKILLS),
                             "existing project skills must be refreshed (replaced)")
            setup_harness("claude", root=d, scope="project", profile="standard",
                          assume_yes=True, home=home, out=lambda _: None)
            with open(stale, encoding="utf-8") as fh:
                self.assertNotIn("STALE OLD CONTENT", fh.read(),
                                 "the stale copy must be overwritten")
            for name in CURATED_SKILLS:
                self.assertTrue(
                    os.path.isfile(os.path.join(d, ".claude", "skills", name, "SKILL.md")),
                    f"skill '{name}' must be (re)written on refresh")


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


class TestSkillsSyncPrune(unittest.TestCase):
    """Stage 5 — setup/update is a SYNC: after writing the current curated skills it PRUNES any
    mokata-authored (marker-bearing) skill dropped from the set, so users stop seeing old/removed
    skills. A user's own (non-marker) skill is NEVER removed; the removal is shown in the plan."""

    def test_setup_prunes_orphan_mokata_skill_keeps_user_skill(self):
        with tempfile.TemporaryDirectory() as d:
            sk = os.path.join(d, ".claude", "skills")
            os.makedirs(os.path.join(sk, "oldremoved"))
            with open(os.path.join(sk, "oldremoved", "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write("--- " + SKILL_MARKER + " ---\nstale body")   # a mokata skill, not curated
            os.makedirs(os.path.join(sk, "myskill"))
            with open(os.path.join(sk, "myskill", "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write("my own skill, no marker")                     # a user's own skill
            res = setup_harness("claude", root=d, scope="project", assume_yes=True,
                                out=lambda _: None)
            self.assertFalse(os.path.exists(os.path.join(sk, "oldremoved")),
                             "the orphan mokata skill dir must be pruned")
            self.assertTrue(os.path.isfile(os.path.join(sk, "myskill", "SKILL.md")),
                            "a user's own (non-marker) skill must be preserved")
            for name in CURATED_SKILLS:
                self.assertTrue(os.path.isfile(os.path.join(sk, name, "SKILL.md")),
                                f"current curated skill '{name}' must be present after sync")
            self.assertTrue(
                any(os.path.join("oldremoved", "SKILL.md") in p for p in res.touched),
                "the pruned skill path must be recorded in the setup result")

    def test_plan_shows_the_removal_before_approval(self):
        with tempfile.TemporaryDirectory() as d:
            sk = os.path.join(d, ".claude", "skills")
            os.makedirs(os.path.join(sk, "oldremoved"))
            with open(os.path.join(sk, "oldremoved", "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write(SKILL_MARKER + "\nold")
            plan = plan_setup("claude", root=d, scope="project")
            self.assertEqual(plan.skill_orphans, ["oldremoved"])
            from mokata.harness_setup import render_setup_plan
            render = render_setup_plan(plan)
            self.assertIn("oldremoved", render)
            self.assertRegex(render.lower(), r"removing|stale")

    def test_prune_never_touches_a_non_marker_user_skill(self):
        from pathlib import Path
        from mokata.agent_skills import prune_orphan_skills
        with tempfile.TemporaryDirectory() as d:
            sk = Path(d) / "skills"
            (sk / "mine").mkdir(parents=True)
            (sk / "mine" / "SKILL.md").write_text("no marker here", encoding="utf-8")
            removed = prune_orphan_skills(sk, keep=())   # keep NOTHING → a user skill still survives
            self.assertEqual(removed, [])
            self.assertTrue((sk / "mine" / "SKILL.md").is_file())

    def test_prune_skips_unreadable_skill_without_crashing(self):
        # A SKILL.md that can't be read (here: it's a directory → IsADirectoryError, an OSError)
        # must be skipped, not crash the sync.
        from pathlib import Path
        from mokata.agent_skills import prune_orphan_skills
        with tempfile.TemporaryDirectory() as d:
            sk = Path(d) / "skills"
            (sk / "weird" / "SKILL.md").mkdir(parents=True)   # SKILL.md is a dir → unreadable
            removed = prune_orphan_skills(sk, keep=())         # must not raise
            self.assertEqual(removed, [])
            self.assertTrue((sk / "weird" / "SKILL.md").is_dir(), "the unreadable entry is left alone")

    def test_regen_prunes_marker_bearing_skill_no_longer_curated(self):
        # The shipped-tree self-heal: regen writes the curated set then prunes dropped ones. Test
        # the shared helper the way `_regenerate_plugin_skills` uses it (keep=CURATED_SKILLS).
        from pathlib import Path
        from mokata.agent_skills import prune_orphan_skills
        with tempfile.TemporaryDirectory() as d:
            sk = Path(d) / "skills"
            (sk / "dropped").mkdir(parents=True)              # a removed/renamed mokata skill
            (sk / "dropped" / "SKILL.md").write_text("--- " + SKILL_MARKER + " ---\nx", encoding="utf-8")
            keep_name = CURATED_SKILLS[0]
            (sk / keep_name).mkdir(parents=True)              # a real curated skill (kept)
            (sk / keep_name / "SKILL.md").write_text("--- " + SKILL_MARKER + " ---\ny", encoding="utf-8")
            removed = prune_orphan_skills(sk, CURATED_SKILLS)
            self.assertEqual([p.parent.name for p in removed], ["dropped"])
            self.assertFalse((sk / "dropped").exists())
            self.assertTrue((sk / keep_name / "SKILL.md").is_file())

    def test_shipped_src_skills_have_no_orphans(self):
        # After regen, the shipped tree matches the KEEP-set (curated pipeline + shipped domain
        # skills) exactly — no marker-bearing orphan ships. Domain skills are marker-bearing but
        # curated-excluded, so they must be in the keep-set (installed_skill_names), not orphans.
        from pathlib import Path
        from mokata.agent_skills import find_orphan_skills, installed_skill_names
        self.assertEqual(find_orphan_skills(Path(_SKILLS_DIR), installed_skill_names()), [])


class TestCuratedCatalog(unittest.TestCase):
    """CAT.S1 — `mokata skills` must present the COMPLETE curated catalog (all 16), not just the
    runnable pipeline skills. `curated_catalog()` is the single source: the set + count follow
    CURATED_SKILLS, and each non-runnable skill's summary is single-sourced from its command
    template frontmatter `description` (so it can't drift from the shipped SKILL.md)."""

    def _catalog(self):
        from pathlib import Path
        return curated_catalog(Path(_TEMPLATES))

    def test_count_and_set_track_curated_skills(self):
        entries = self._catalog()
        # count is single-sourced to CURATED_SKILLS — never a hardcoded number
        self.assertEqual(len(entries), len(CURATED_SKILLS))
        self.assertEqual([e.name for e in entries], list(CURATED_SKILLS))

    def test_previously_omitted_skills_are_present(self):
        by_name = {e.name: e for e in self._catalog()}
        for name in ("govern", "session", "playbook", "mcp-repair", "docsync"):
            self.assertIn(name, by_name, f"{name} must appear in the curated catalog")

    def test_non_runnable_summary_single_sourced_from_template(self):
        # the 5 non-_SKILLS curated skills carry their command-template `description`, not a copy
        by_name = {e.name: e for e in self._catalog()}
        for name in ("govern", "session", "playbook", "mcp-repair", "docsync"):
            entry = by_name[name]
            self.assertFalse(entry.runnable, f"{name} is not runnable via `mokata run`")
            fm = parse_frontmatter(_read(os.path.join(_TEMPLATES, f"{name}.md")))
            self.assertEqual(entry.summary, fm["description"])
            # invocation points at its own command / auto-fire, not `mokata run`
            self.assertNotIn("mokata run", entry.invocation)
            self.assertIn(name, entry.invocation)

    def test_runnable_pipeline_summary_from_skills_registry(self):
        from mokata.skills import SKILLS
        by_name = {e.name: e for e in self._catalog()}
        for name in ("spec", "test", "develop", "review", "brainstorm"):
            entry = by_name[name]
            self.assertTrue(entry.runnable)
            self.assertEqual(entry.summary, SKILLS[name].summary)
            self.assertIn("mokata run", entry.invocation)


if __name__ == "__main__":
    unittest.main()
