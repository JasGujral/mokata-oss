"""L1 — `/brainstorm` (here: `mokata brainstorm`) runs standalone, with no prior
pipeline phase required. Also checks the shipped command template carries the
clean-room prompt devices."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.cli import main
from mokata.init import init_repo


def silent(_):
    pass


class TestBrainstormCLI(unittest.TestCase):
    def test_runs_standalone_on_a_fresh_repo(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["brainstorm", "--path", d])
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            # the HARD-GATE device and one-question discipline are surfaced
            self.assertIn("HARD-GATE", out)
            self.assertIn("one question", out.lower())
            # live grounding is shown (standard wires graph + memory)
            self.assertIn("grounding", out.lower())

    def test_minimal_profile_brainstorm_still_runs_and_shows_degradation(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="minimal", assume_yes=True, out=silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["brainstorm", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("HARD-GATE", buf.getvalue())

    def test_status_reports_no_approach_yet(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["brainstorm", "--status", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("no approved approach", buf.getvalue().lower())


class TestBrainstormCommandTemplate(unittest.TestCase):
    def test_command_template_carries_the_devices(self):
        here = os.path.dirname(__file__)
        path = os.path.join(here, "..", "templates", "commands", "brainstorm.md")
        self.assertTrue(os.path.exists(path), "brainstorm command template missing")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("HARD-GATE", text)
        self.assertIn("one question", text.lower())
        self.assertIn("approval", text.lower())
        self.assertIn("tradeoff", text.lower())


if __name__ == "__main__":
    unittest.main()
