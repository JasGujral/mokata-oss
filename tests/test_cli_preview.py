"""E7 at the command path — `mokata preview` is a dry-run: it lists the plan and writes
nothing."""

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


class TestPreviewCLI(unittest.TestCase):
    def test_preview_runs_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            state_dir = os.path.join(d, ".mokata", "state")
            before = os.path.exists(state_dir)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["preview", "--path", d])
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("emit", out)
            self.assertIn("completeness", out)
            # dry-run: no pipeline state was created
            self.assertEqual(before, os.path.exists(state_dir))


if __name__ == "__main__":
    unittest.main()
