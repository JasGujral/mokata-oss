"""Stage 20 — governance enforcement end-to-end: secret-block + trust dial.

Every durable write goes through one gate. A secret is a hard security block that human
approval cannot override; the per-tool trust dial decides whether a tool may write at all
(read-only), must always be surfaced (propose-only), or may be auto-approved (gated-write).
The sync secret-guard hook maps a detected secret to exit code 2. Each decision is recorded
in the audit ledger.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest

from _support import write_sample_repo  # noqa: F401  (import = path-shim side effect)

from mokata.govern import (AuditLedger, GATED_WRITE, PROPOSE_ONLY, READ_ONLY,
                           TrustPolicy, WriteGate, WriteRequest)

# A realistic-looking-but-fake AWS access key id (matches the AKIA signature only).
FAKE_SECRET = "AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'"
CLEAN = "print('hello world')"


def _hook_main():
    """Load hooks/secret_guard.py by path and return its main()."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "..", "hooks", "secret_guard.py")
    spec = importlib.util.spec_from_file_location("_mokata_secret_guard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main


class TestSecretBlock(unittest.TestCase):
    def test_secret_is_blocked_even_with_approval(self):
        gate = WriteGate()
        out = gate.submit(WriteRequest("code", "/app.py", FAKE_SECRET),
                          assume_yes=True)          # approval cannot override security
        self.assertFalse(out.committed)
        self.assertTrue(out.aborted)
        self.assertTrue(out.findings)

    def test_clean_content_commits(self):
        committed = {}
        gate = WriteGate()
        out = gate.submit(WriteRequest("code", "/app.py", CLEAN),
                          commit=lambda: committed.setdefault("done", True),
                          assume_yes=True)
        self.assertTrue(out.committed)
        self.assertTrue(committed.get("done"))

    def test_secret_guard_hook_exit_codes(self):
        main = _hook_main()
        with contextlib.redirect_stderr(io.StringIO()):     # hook logs blocks to stderr
            self.assertEqual(main(["--text", FAKE_SECRET, "--path", "/app.py"]), 2)
            self.assertEqual(main(["--text", CLEAN]), 0)


class TestTrustDial(unittest.TestCase):
    def test_read_only_tool_cannot_write(self):
        gate = WriteGate(trust=TrustPolicy({"linter": READ_ONLY}))
        out = gate.submit(WriteRequest("config", "/x.json", "value", tool="linter"),
                          assume_yes=True)
        self.assertFalse(out.committed)
        self.assertIn("read-only", out.reason)

    def test_propose_only_tool_is_never_auto_approved(self):
        gate = WriteGate(trust=TrustPolicy({"agent": PROPOSE_ONLY}))
        # assume_yes is ignored for propose-only — an explicit decline blocks it
        declined = gate.submit(WriteRequest("config", "/x.json", "value", tool="agent"),
                               assume_yes=True, confirm=lambda _: False)
        self.assertFalse(declined.committed)
        # an explicit human yes lets it through
        approved = gate.submit(WriteRequest("config", "/x.json", "value", tool="agent"),
                               assume_yes=True, confirm=lambda _: True)
        self.assertTrue(approved.committed)

    def test_gated_write_tool_may_be_auto_approved(self):
        gate = WriteGate(trust=TrustPolicy({"agent": GATED_WRITE}))
        out = gate.submit(WriteRequest("config", "/x.json", "value", tool="agent"),
                          assume_yes=True)
        self.assertTrue(out.committed)

    def test_every_decision_is_recorded_in_the_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = AuditLedger.from_mokata_dir(d)
            gate = WriteGate(ledger=ledger,
                             trust=TrustPolicy({"linter": READ_ONLY}))
            gate.submit(WriteRequest("config", "/x.json", "value", tool="linter"),
                        assume_yes=True)                                  # blocked
            gate.submit(WriteRequest("code", "/app.py", FAKE_SECRET),
                        assume_yes=True)                                  # blocked: secret
            gate.submit(WriteRequest("code", "/app.py", CLEAN),
                        assume_yes=True)                                  # approved

            decisions = [e.get("decision") for e in ledger.entries()
                         if e.get("kind") == "write_gate"]
            self.assertIn("blocked", decisions)
            self.assertIn("approved", decisions)


if __name__ == "__main__":
    unittest.main()
