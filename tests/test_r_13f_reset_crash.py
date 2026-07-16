"""R-13F · RESET-CRASH — the MCP reset redemption must survive the deletion of its own ledger.

The MCP reset is propose → approve → redeem (SI.3). On redeem, `_gated_write` commits `_do_reset`
(which deletes `.mokata`, and with it the repo's audit ledger) and THEN writes the approved record
to that ledger — so `AuditLedger.record` opened a path that no longer existed and raised
`FileNotFoundError`. The removal happened and the user-scoped tombstone survived, but the tool
crashed and the redemption never returned.

This full round-trip test FAILS (errors with FileNotFoundError) on pre-fix `src/`. The CLI reset —
which calls `reset_state` directly, never through the gate — is unaffected and unchanged.
"""

import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import MOKATA_DIR, config_cmd, session
from mokata.govern import lifecycle
from mokata.init import init_repo
from mokata.mcp import tools_approve as TA
from mokata.mcp import tools_write as TW


class TestResetCrash(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.path = self._dir.name
        init_repo(root=self.path, profile="standard", assume_yes=True, out=lambda *_a: None)
        self._env = mock.patch.dict(os.environ, {session.SESSION_ID_ENV: "run-reset"})
        self._env.start()
        self.addCleanup(self._env.stop)
        session.reset_for_test()
        self.addCleanup(session.reset_for_test)
        # The human's own gated config write — enables the in-chat approve surface (AP-MCP).
        r = config_cmd.config_set(self.path, "settings.approvals.in_chat", "true",
                                  assume_yes=True, out=lambda *_a: None)
        assert r.committed, r.message
        self.tomb = os.path.join(self._home.name, ".mokata", "removals.json")

    def test_reset_crash_regression(self):
        mdir = os.path.join(self.path, MOKATA_DIR)
        with mock.patch.object(lifecycle, "_tombstone_path", return_value=self.tomb):
            proposed = TW.reset(path=self.path)
            self.assertEqual(proposed["status"], "proposed", proposed)
            pid = proposed["proposal_id"]

            approved = TA.approve(path=self.path, proposal_id=pid)
            self.assertTrue(approved.get("approved"), approved)

            # THE redemption — on pre-fix src/ this raises FileNotFoundError.
            redeemed = TW.reset(path=self.path, proposal_id=pid)

        self.assertEqual(redeemed["status"], "committed", redeemed)
        self.assertTrue(redeemed["committed"])
        self.assertTrue(redeemed["result"]["removed"], "the redemption reports what it removed")
        self.assertFalse(os.path.exists(mdir), "the removal happened — .mokata is gone")
        self.assertTrue(os.path.exists(self.tomb),
                        "the durable removal record (tombstone) survives the reset")


if __name__ == "__main__":
    unittest.main()
