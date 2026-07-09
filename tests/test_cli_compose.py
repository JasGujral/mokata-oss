"""CLI surface for L1/L2/L3/L4: `mokata skills` (catalog + detail), `mokata run`
(standalone skill), `mokata enter` (mid-pipeline entry)."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.cli import main
from mokata.init import init_repo


def silent(_):
    pass


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class TestSkillsCatalog(unittest.TestCase):
    def test_skills_lists_catalog_without_full_prompts(self):
        rc, out = run_cli(["skills"])
        self.assertEqual(rc, 0)
        self.assertIn("spec", out)
        self.assertIn("test", out)
        # progressive disclosure: the list does not dump full prompt bodies
        self.assertNotIn("watch them FAIL first", out)

    def test_skills_detail_reveals_more(self):
        rc, out = run_cli(["skills", "test"])
        self.assertEqual(rc, 0)
        self.assertIn("RED", out)
        self.assertIn("red-before-green", out)

    def test_skills_lists_all_16_curated_grouped(self):
        # CAT.S1 — the bare list is the COMPLETE curated catalog, including the 5 that
        # `list_skills()` (the 12 runnable) omits, grouped runnable vs own-command.
        from mokata.agent_skills import CURATED_SKILLS
        rc, out = run_cli(["skills"])
        self.assertEqual(rc, 0)
        for name in CURATED_SKILLS:
            self.assertIn(name, out, f"{name} missing from `mokata skills`")
        for name in ("govern", "session", "playbook", "mcp-repair", "docsync"):
            self.assertIn(name, out)
        # grouping is visible: a runnable group and an auto-firing / own-command group
        self.assertIn("mokata run", out)
        self.assertRegex(out, r"(?i)auto-fir|own[- ]command|standalone")

    def test_skills_detail_works_for_non_runnable(self):
        for name in ("docsync", "govern"):
            rc, out = run_cli(["skills", name])
            self.assertEqual(rc, 0, f"`mokata skills {name}` should not error")
            self.assertIn(name, out)
            self.assertNotIn("no skill", out.lower())

    def test_skills_search_finds_previously_omitted(self):
        rc, out = run_cli(["skills", "search", "docsync"])
        self.assertEqual(rc, 0)
        self.assertIn("docsync", out)

    def test_run_non_runnable_gives_clear_message_not_crash(self):
        import contextlib
        outbuf, errbuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(outbuf), contextlib.redirect_stderr(errbuf):
            rc = main(["run", "govern"])
        combined = outbuf.getvalue() + errbuf.getvalue()
        self.assertNotEqual(rc, 0)
        # a clear message pointing at the real invocation, not an argparse/KeyError crash
        self.assertIn("govern", combined)
        self.assertIn("/mokata:govern", combined)


class TestRunStandalone(unittest.TestCase):
    def test_run_works_with_no_init_and_no_pipeline_prerequisite(self):
        # truly standalone: no repo init, no upstream phase. (Implementation skills
        # develop/test now require a persisted spec — Stage 32 — so this uses `spec`,
        # which is genuinely standalone.)
        rc, out = run_cli(["run", "spec"])
        self.assertEqual(rc, 0)
        self.assertIn("standalone", out.lower())
        self.assertIn("Gate", out)

    def test_run_grounds_when_initialized(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            rc, out = run_cli(["run", "review", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("Grounding", out)


class TestEnterMidPipeline(unittest.TestCase):
    def test_enter_applies_only_that_phase_gate(self):
        rc, out = run_cli(["enter", "completeness_gate"])
        self.assertEqual(rc, 0)
        self.assertIn("completeness", out)
        # brainstorm is upstream and is shown skipped, not forced
        self.assertIn("skipped", out.lower())
        self.assertIn("brainstorm", out)


if __name__ == "__main__":
    unittest.main()
