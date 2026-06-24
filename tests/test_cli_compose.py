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


class TestRunStandalone(unittest.TestCase):
    def test_run_works_with_no_init_and_no_pipeline_prerequisite(self):
        # truly standalone: no repo init, no upstream phase
        rc, out = run_cli(["run", "test"])
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
