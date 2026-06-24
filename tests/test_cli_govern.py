"""CLI surface for G1/I3 — `mokata rules` (tiers + line counts) and `mokata audit`
(the ledger)."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.cli import main
from mokata.govern import AuditLedger
from mokata.init import init_repo
from mokata import MOKATA_DIR


def silent(_):
    pass


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


class TestRulesCLI(unittest.TestCase):
    def test_rules_shows_tiers_and_line_budget(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            rc, out = run_cli(["rules", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("always_on", out)
            self.assertIn("60", out)        # the cap is shown
            self.assertIn("articles", out)


class TestAuditCLI(unittest.TestCase):
    def test_audit_shows_ledger_entries(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            led = AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))
            led.record("gate", decision="approve", target="spec")
            rc, out = run_cli(["audit", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("gate", out)
            self.assertIn("spec", out)


if __name__ == "__main__":
    unittest.main()
