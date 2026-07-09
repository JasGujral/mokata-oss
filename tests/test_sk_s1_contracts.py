"""SK.S1 — per-skill Contracts + the single-sourced `⛭` activation line.

Guards, all over the 15 CURATED_SKILLS:
  * every SKILL.md carries a `## Contract` block AND a `⛭ … active — gate:` line;
  * the activation line is EXACTLY `progress.active_skill_line(name)` — single source, not a copy;
  * the `⛭` glyph is authored in exactly ONE module (progress.py), never hardcoded 15 times;
  * a planted drift in a shipped SKILL.md is CAUGHT by the drift guard;
  * regeneration is idempotent (render twice → identical);
  * NO Contract clause cites an enforcement the map didn't back (backed gates only);
  * every backed gate's enforcement-point file actually exists on disk.
"""

import os
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata.agent_skills import CURATED_SKILLS, generate_skill_files, skill_markdown
from mokata.progress import SKILL_GLYPH, active_skill_line
from mokata import skill_contracts as sc

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SKILLS_DIR = os.path.join(_REPO, "src", "mokata", "skills")
_TEMPLATES = Path(os.path.join(_REPO, "src", "mokata", "templates", "commands"))


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestContractSourceCoversAll15(unittest.TestCase):
    def test_every_curated_skill_has_a_contract(self):
        self.assertEqual(set(sc.CONTRACTS), set(CURATED_SKILLS),
                         "CONTRACTS must cover exactly the 15 curated skills")

    def test_every_headline_gate_is_a_known_gate(self):
        for name, c in sc.CONTRACTS.items():
            with self.subTest(skill=name):
                self.assertIn(c.headline_gate, sc.GATES,
                              f"{name}'s headline gate is not in the gate registry")


class TestShippedSkillsCarryContractAndActivation(unittest.TestCase):
    def test_all_15_have_contract_and_activation_line(self):
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                text = _read(os.path.join(_SKILLS_DIR, name, "SKILL.md"))
                self.assertIn("## Contract", text, f"{name} is missing its ## Contract block")
                self.assertIn(active_skill_line(name), text,
                              f"{name} is missing/altered its ⛭ activation line")
                # the activation line must be the single-source string, verbatim
                self.assertIn(f"{SKILL_GLYPH} mokata {name} active — gate:", text)


class TestActivationLineIsSingleSourced(unittest.TestCase):
    """No 15 copies to drift: the ⛭ line each SKILL.md carries IS `active_skill_line(name)`, and
    the glyph is authored in exactly ONE source module."""

    def test_shipped_activation_line_equals_single_source(self):
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                text = _read(os.path.join(_SKILLS_DIR, name, "SKILL.md"))
                # exactly one activation line, and it equals the single source
                lines = [ln for ln in text.splitlines() if ln.startswith(SKILL_GLYPH)]
                self.assertEqual(len(lines), 1, f"{name} must carry exactly one ⛭ line")
                self.assertEqual(lines[0], active_skill_line(name))

    def test_glyph_constant_defined_in_exactly_one_module(self):
        # The single source: the `SKILL_GLYPH = "⛭"` constant is ASSIGNED only in progress.py, and
        # the renderer DELEGATES to active_skill_line rather than constructing its own line.
        src = os.path.join(_REPO, "src", "mokata")
        assigners = [rel for rel in os.listdir(src)
                     if rel.endswith(".py")
                     and f'SKILL_GLYPH = "{SKILL_GLYPH}"' in _read(os.path.join(src, rel))]
        self.assertEqual(assigners, ["progress.py"],
                         "the ⛭ activation glyph must be assigned in exactly one module")

    def test_renderer_delegates_to_the_single_source(self):
        # agent_skills.render_skill_md must call active_skill_line (not build its own ⛭ line), so
        # the activation line can never fork into a per-skill copy.
        agent_skills_src = _read(os.path.join(_REPO, "src", "mokata", "agent_skills.py"))
        self.assertIn("active_skill_line", agent_skills_src,
                      "the SKILL.md renderer must delegate to progress.active_skill_line")
        # and it must not construct an activation line itself (no ⛭ inside an f-string/return)
        self.assertNotIn(f'"{SKILL_GLYPH}', agent_skills_src,
                         "agent_skills must not build a ⛭ line literal — it delegates")

    def test_templates_do_not_hardcode_activation_lines(self):
        # the ⛭ line is injected at render time, never hand-written into a command template
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                self.assertNotIn(SKILL_GLYPH, _read(str(_TEMPLATES / f"{name}.md")),
                                 f"{name}.md template must not hardcode the ⛭ activation line")

    def test_gate_copy_comes_from_the_contract_source(self):
        # The activation line's gate: copy is the Contract's headline gate one-liner — one source.
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                self.assertIn(sc.headline_gate_line(name), active_skill_line(name))


class TestDriftGuard(unittest.TestCase):
    """A shipped SKILL.md is byte-identical to what the builder renders — Contract + activation
    included. Planting a drift must be caught; regeneration must be idempotent."""

    def test_shipped_matches_generated(self):
        for name in CURATED_SKILLS:
            with self.subTest(skill=name):
                shipped = _read(os.path.join(_SKILLS_DIR, name, "SKILL.md"))
                self.assertEqual(shipped, skill_markdown(name, _TEMPLATES),
                                 f"{name}/SKILL.md drifted — regenerate, never hand-edit")

    def test_planted_activation_drift_is_caught(self):
        # Simulate a hand-edit that silently changes an activation line, and prove the guard fails.
        name = "brainstorm"
        shipped = _read(os.path.join(_SKILLS_DIR, name, "SKILL.md"))
        tampered = shipped.replace(active_skill_line(name),
                                   f"{SKILL_GLYPH} mokata brainstorm active — gate: ANYTHING GOES")
        self.assertNotEqual(tampered, shipped, "the tamper must actually change the text")
        self.assertNotEqual(tampered, skill_markdown(name, _TEMPLATES),
                            "the drift guard must reject a tampered activation line")

    def test_planted_contract_drift_is_caught(self):
        name = "develop"
        shipped = _read(os.path.join(_SKILLS_DIR, name, "SKILL.md"))
        # weaken a Contract boundary by hand
        tampered = shipped.replace("- write code without a persisted spec (gate: spec-persisted)",
                                   "- write code freely (advisory)")
        self.assertNotEqual(tampered, shipped)
        self.assertNotEqual(tampered, skill_markdown(name, _TEMPLATES),
                            "the drift guard must reject a tampered Contract")

    def test_regeneration_is_idempotent(self):
        once = generate_skill_files(_TEMPLATES)
        twice = generate_skill_files(_TEMPLATES)
        self.assertEqual(once, twice, "rendering twice must produce identical output")
        # and both equal what is shipped
        for name, content in once.items():
            self.assertEqual(content, _read(os.path.join(_SKILLS_DIR, name, "SKILL.md")))


class TestNoUnbackedEnforcement(unittest.TestCase):
    """The load-bearing invariant: a Contract may cite a gate ONLY if it is a real, executable
    enforcement point. Advisory boundaries carry no gate id."""

    def test_no_contract_cites_an_unbacked_gate(self):
        self.assertEqual(sc.unbacked_citations(), [],
                         "a Contract cited a gate that is not a backed enforcement point")

    def test_every_cited_gate_is_backed_and_exists_on_disk(self):
        for gate_id in sc.cited_gate_ids():
            with self.subTest(gate=gate_id):
                ref = sc.GATES[gate_id]
                self.assertTrue(ref.backed, f"cited gate {gate_id} must be backed")
                path = os.path.join(_REPO, ref.enforcement_point)
                self.assertTrue(os.path.isfile(path),
                                f"{gate_id} names a missing enforcement point: {ref.enforcement_point}")

    def test_rendering_raises_if_a_clause_cites_a_nonbacked_gate(self):
        # Guard the guard: a clause that cites an advisory gate id must raise at render time.
        bad = sc.Contract(skill="x", headline_gate="write-gate",
                          can=("do a thing",),
                          must_not=(sc.Clause("nope", gate="approach-approval"),),  # advisory!
                          depends_on=())
        saved = sc.CONTRACTS.get("x")
        sc.CONTRACTS["x"] = bad
        try:
            with self.assertRaises(ValueError):
                sc.render_contract_md("x")
        finally:
            if saved is None:
                del sc.CONTRACTS["x"]
            else:
                sc.CONTRACTS["x"] = saved

    def test_prose_only_headline_flags_match_the_map(self):
        # These skills announce a protocol HARD-GATE (advisory headline), per doc 76's flags.
        prose_only = set(sc.prose_only_headline_skills())
        expected = {"brainstorm", "refine", "test", "debug", "bug", "optimize", "review",
                    "ship", "onboard", "govern", "session", "playbook", "mcp-repair",
                    "docsync"}
        self.assertEqual(prose_only, expected,
                         "prose-only headline set drifted from the SK.S1 map (doc 76)")


class TestNoShippedSkillLeaksAnInternalDocPath(unittest.TestCase):
    """OSS public boundary (FOOTER-1 regression guard): SKILL.md ship PUBLIC but `docs/build/`,
    `docs/launch/`, and `docs/marketing/` are EXCLUDED from the public mirror. So no shipped
    SKILL.md — the 15 pipeline skills PLUS the domain skills (api/security from DK.S1 +
    performance/frontend-a11y/browser-testing from DK.S2 + ci-cd/git from DK.S3 +
    deprecation/docs-adr/shipping from DK.S4 — the full 10-domain attachment map), 25 in all — may
    reference an internal doc path, or OSS users get a dangling reference. The Contract's
    `> Grounding:` footer is the single place this once leaked (`docs/build/76-…`)."""

    def _all_shipped_skill_names(self):
        from mokata.domains import shipped_domain_skills
        return tuple(CURATED_SKILLS) + tuple(shipped_domain_skills())

    def test_all_25_ship(self):
        names = self._all_shipped_skill_names()
        self.assertEqual(len(names), len(CURATED_SKILLS) + 10,
                         "expected 15 pipeline + 10 domain = 25 shipped SKILL.md")
        for name in names:
            with self.subTest(skill=name):
                self.assertTrue(os.path.isfile(os.path.join(_SKILLS_DIR, name, "SKILL.md")))

    def test_no_shipped_skill_md_references_an_internal_doc_path(self):
        internal = ("docs/build/", "docs/launch/", "docs/marketing/")
        for name in self._all_shipped_skill_names():
            text = _read(os.path.join(_SKILLS_DIR, name, "SKILL.md"))
            with self.subTest(skill=name):
                for path in internal:
                    self.assertNotIn(
                        path, text,
                        f"{name}/SKILL.md references internal path '{path}' — ships public but "
                        f"the mirror excludes it, so this is a dangling reference for OSS users")

    def test_footer_still_states_the_gate_vs_advisory_distinction(self):
        # The correction drops the internal-path clause but must KEEP the meaning.
        footer = sc.render_contract_md("develop").splitlines()[-1]
        self.assertIn("(gate: …)", footer)
        self.assertIn("enforced by that gate in code", footer)
        self.assertIn("advisory", footer)
        self.assertIn("not a hard block", footer)
        self.assertNotIn("docs/build", footer)


if __name__ == "__main__":
    unittest.main()
