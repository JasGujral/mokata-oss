"""F5 at the command path — `mokata budget` shows live savings + statusline."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MOKATA_DIR
from mokata.cli import main
from mokata.govern import AuditLedger, SavingsTracker
from mokata.init import init_repo


def silent(_):
    pass


class TestBudgetCLI(unittest.TestCase):
    def test_budget_shows_savings_and_statusline(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            led = AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))
            SavingsTracker(ledger=led).record("jit-retrieval", 120, 40)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["budget", "--path", d])
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("saved", out.lower())
            self.assertIn("80", out)        # 120 - 40

    def test_budget_empty_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["budget", "--path", d])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
