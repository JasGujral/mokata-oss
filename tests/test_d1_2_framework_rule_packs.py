"""0.0.25 Lane D1 stage 2 — React + NestJS rule packs; frontend perf + security skills.

What must be true for the user:
  * `frontend-react` and `backend-nestjs` ship as INDEXED RULE PACKS: a short SKILL.md that indexes
    one-rule files, impact-ordered, each rule with an Incorrect/Correct pair, a way to verify it and
    a cited primary source — and version branches (React ≥19 / Compiler on|off; NestJS's
    ESM / Vitest / Standard Schema major) that point at the project's own docs instead of embedding
    version facts;
  * `frontend-perf` runs measure → identify → fix → verify (keep or revert) → guard, and
    `frontend-security` puts AUTHORIZATION first; both are audit-first and carry a
    Rationalization | Reality table;
  * every one is a full mokata domain skill (Contract, ⛭ line, triggers + a negative, verification,
    citations) that setup delivers — and none claims a gate that is not backed in code (doc 85 §4).
"""
import os
import re
import tempfile
import unittest
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import domains as dk
from mokata import skill_lints as sl
from mokata.agent_skills import (SKILL_MARKER, install_domain_skills, installed_skill_names,
                                 parse_frontmatter)
from mokata.skill_contracts import GATES

_SKILLS = Path(__file__).resolve().parent.parent / "src" / "mokata" / "skills"

BATCH = ("frontend-react", "backend-nestjs", "frontend-perf", "frontend-security")
RULE_PACKS = ("frontend-react", "backend-nestjs")
IMPACTS = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
# The gates another 0.0.25 lane owns. Until one is backed in `skill_contracts.GATES`, no Contract may
# cite it (doc 85 §4) — this batch must not pre-empt them.
OTHER_LANE_GATES = ("bundle-budget", "render-regression", "bundle-secret-scan",
                    "dep-vulnerability", "dep-cooldown", "route-guarded", "react-lint")


def _text(name, rel="SKILL.md"):
    return (_SKILLS / name / rel).read_text(encoding="utf-8")


def _index_rows(name):
    """(impact, rule-file) for each row of the SKILL.md rule table, in order."""
    rows = []
    for line in _text(name).splitlines():
        m = re.match(r"^\|\s*(CRITICAL|HIGH|MEDIUM|LOW)\s*\|\s*`([a-z0-9-]+\.md)`", line)
        if m:
            rows.append((m.group(1), m.group(2)))
    return rows


class TheBatchShipsAsDomainSkills(unittest.TestCase):

    def test_the_framework_batch_follows_the_attachment_map_in_registry_order(self):
        self.assertEqual(dk.shipped_domain_skills()[10:], BATCH)
        for name in BATCH:
            self.assertIn(name, installed_skill_names(), "setup must keep it, never prune it")

    def test_each_is_a_full_domain_skill_with_no_lint_finding(self):
        texts = {n: _text(n) for n in BATCH}
        for n, t in texts.items():
            self.assertIn(SKILL_MARKER, t)
            fm = parse_frontmatter(t)
            self.assertEqual(n, fm.get("name"))
            self.assertTrue(fm.get("description", "").startswith("mokata ·"))
            self.assertLess(len(t.splitlines()), 500, "SKILL.md stays an index, not a manual")
        findings = sl.run_lints(texts=texts)
        self.assertEqual([], findings, "\n".join(f.render() for f in findings))

    def test_the_activation_line_is_single_sourced(self):
        for n in BATCH:
            lines = [ln.strip() for ln in _text(n).splitlines() if ln.strip().startswith("⛭")]
            self.assertEqual([dk.domain_active_skill_line(n)], lines)

    def test_the_wheel_globs_ship_every_reference_file(self):
        # Measured before this stage: a wheel built from v0.0.24 carried 0 `references/` files —
        # `skills/*/*` stops at the directory. The rule packs ARE their references, so the
        # package-data globs must match every one (setuptools expands them relative to the package).
        import glob
        pyproject = (_SKILLS.parent.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
        block = pyproject.split("[tool.setuptools.package-data]", 1)[1].split("\n[", 1)[0]
        globs = re.findall(r'^\s*"([^"]+)",', block, re.M)
        pkg = _SKILLS.parent
        shipped = {Path(p).resolve() for g in globs for p in glob.glob(str(pkg / g))
                   if Path(p).is_file()}
        for n in BATCH + ("frontend-a11y",):
            for ref in (_SKILLS / n / "references").iterdir():
                self.assertIn(ref.resolve(), shipped, f"{n}/references/{ref.name} not packaged")

    def test_setup_delivers_every_reference_file(self):
        with tempfile.TemporaryDirectory() as d:
            written = install_domain_skills(_SKILLS, Path(d), names=BATCH)
            got = {p.relative_to(d).as_posix() for p in written}
            for n in BATCH:
                for ref in (_SKILLS / n / "references").iterdir():
                    self.assertIn(f"{n}/references/{ref.name}", got)


class TheRulePacksAreIndexedAndImpactOrdered(unittest.TestCase):

    def test_the_index_names_every_rule_file_and_nothing_else(self):
        for n in RULE_PACKS:
            with self.subTest(pack=n):
                indexed = [f for _i, f in _index_rows(n)]
                on_disk = sorted(p.name for p in (_SKILLS / n / "references").iterdir())
                self.assertEqual(sorted(indexed), on_disk, "no orphan rule, no dangling index row")
                self.assertGreaterEqual(len(indexed), 8)

    def test_rows_run_from_highest_impact_to_lowest(self):
        for n in RULE_PACKS:
            ranks = [IMPACTS.index(i) for i, _f in _index_rows(n)]
            self.assertEqual(sorted(ranks), ranks, f"{n}'s index is not impact-ordered")
            self.assertEqual("CRITICAL", _index_rows(n)[0][0])

    def test_every_rule_file_is_one_rule_with_an_incorrect_correct_pair(self):
        for n in RULE_PACKS:
            for impact, fname in _index_rows(n):
                with self.subTest(rule=f"{n}/{fname}"):
                    body = _text(n, f"references/{fname}")
                    self.assertIn(f"**Impact:** {impact}", body, "file and index agree on impact")
                    self.assertEqual(1, body.count("## Incorrect"))
                    self.assertEqual(1, body.count("## Correct"))
                    bad = body.split("## Incorrect", 1)[1].split("## Correct", 1)[0]
                    good = body.split("## Correct", 1)[1].split("## Verify", 1)[0]
                    self.assertIn("```", bad)
                    self.assertIn("```", good)
                    self.assertNotEqual(bad.strip(), good.strip())
                    self.assertIn("## Verify", body)
                    self.assertRegex(body, r"https://")
                    self.assertIn("UNVERIFIED", body)

    def test_references_are_one_level_deep(self):
        for n in BATCH:
            for p in (_SKILLS / n / "references").iterdir():
                self.assertTrue(p.is_file(), f"{p} — references stay one level deep")

    def test_react_branches_on_version_and_compiler_and_reads_the_project(self):
        t = _text("frontend-react")
        for marker in ("React ≥ 19, Compiler ON", "React ≥ 19, Compiler OFF", "React < 19",
                       "package.json", "https://react.dev/"):
            self.assertIn(marker, t)
        self.assertIn("Hunt silent **bailouts**", t)

    def test_nestjs_branches_on_the_esm_vitest_standard_schema_major(self):
        t = _text("backend-nestjs")
        for marker in ("ESM", "Vitest", "Standard Schema", "@nestjs/core", "https://docs.nestjs.com/"):
            self.assertIn(marker, t)
        self.assertEqual("authorize-in-guards-not-middleware.md", _index_rows("backend-nestjs")[0][1])


class PerfAndSecurityAreMeasureFixVerify(unittest.TestCase):

    def test_perf_runs_the_loop_in_order_and_keeps_only_a_measured_win(self):
        t = _text("frontend-perf")
        steps = ["**Measure**", "**Identify**", "**Fix**", "**Verify — keep or revert**", "**Guard**"]
        positions = [t.index(s) for s in steps]
        self.assertEqual(sorted(positions), positions)
        self.assertIn("before:", t)
        self.assertIn("after:", t)
        self.assertIn("Audit first, fix on request", t)
        self.assertIn("INP", t)

    def test_security_puts_authorization_first(self):
        t = _text("frontend-security")
        checklist = t.split("## The checklist", 1)[1]
        first = re.search(r"^1\. \*\*(.+?)\*\*", checklist, re.M).group(1)
        self.assertTrue(first.lower().startswith("authorization"), first)
        self.assertIn("direct", checklist.split("2. ", 1)[0].lower(),
                      "Server Functions are reachable by a direct POST")
        self.assertIn("Audit first, fix on request", t)

    def test_both_carry_a_rationalization_reality_table(self):
        for n in ("frontend-perf", "frontend-security", *RULE_PACKS):
            self.assertIn("| Rationalization | Reality |", _text(n))


class NoContractClaimsAnUnbackedGate(unittest.TestCase):

    def test_every_cited_gate_is_backed_and_no_other_lane_gate_is_cited(self):
        for n in BATCH:
            contract = _text(n).split("## Contract", 1)[1]
            cited = re.findall(r"\(gate: ([a-z0-9-]+)\)", contract)
            self.assertTrue(cited, f"{n} cites no gate")
            for g in cited:
                with self.subTest(skill=n, gate=g):
                    self.assertIn(g, GATES)
                    self.assertTrue(GATES[g].backed, f"{n} cites unbacked gate {g}")
            for g in OTHER_LANE_GATES:
                self.assertNotIn(g, _text(n), f"{n} names {g}, which this lane does not back")

    def test_the_batch_adds_no_gate_and_no_review_axis(self):
        instruments = set(dk.DOMAIN_INSTRUMENT_ONELINE)
        for n in BATCH:
            dom = dk.DOMAIN_REGISTRY[n]
            self.assertTrue(dom.gate in GATES or dom.gate in instruments)
            self.assertIsNone(dom.review_axis)


if __name__ == "__main__":
    unittest.main()
