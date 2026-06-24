"""K3 — per-adapter trust dial, ENFORCED by the WriteGate: read-only blocks writes,
propose-only surfaces them for approval, gated-write is the default."""

import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import (
    GATED_WRITE,
    PROPOSE_ONLY,
    READ_ONLY,
    TrustPolicy,
    WriteGate,
    WriteRequest,
)


def req(tool):
    return WriteRequest("config", "/repo/x.cfg", "value=1", tool=tool)


class TestTrustDial(unittest.TestCase):
    def test_read_only_blocks_writes(self):
        gate = WriteGate(trust=TrustPolicy({"linter": READ_ONLY}))
        committed = []
        out = gate.submit(req("linter"), commit=lambda: committed.append(1),
                          assume_yes=True)            # even with approval
        self.assertFalse(out.committed)
        self.assertEqual(committed, [])
        self.assertIn("read-only", out.reason)

    def test_propose_only_requires_explicit_human_even_with_assume_yes(self):
        gate = WriteGate(trust=TrustPolicy({"agent-x": PROPOSE_ONLY}))
        committed = []
        # assume_yes is ignored for propose-only -> surfaced for approval
        out = gate.submit(req("agent-x"), commit=lambda: committed.append(1),
                          assume_yes=True, confirm=lambda _t: False)
        self.assertFalse(out.committed)
        self.assertEqual(committed, [])

    def test_propose_only_commits_on_explicit_approval(self):
        gate = WriteGate(trust=TrustPolicy({"agent-x": PROPOSE_ONLY}))
        committed = []
        out = gate.submit(req("agent-x"), commit=lambda: committed.append(1),
                          confirm=lambda _t: True)
        self.assertTrue(out.committed)
        self.assertEqual(committed, [1])

    def test_gated_write_allows_auto_approve(self):
        gate = WriteGate(trust=TrustPolicy({"agent-x": GATED_WRITE}))
        committed = []
        out = gate.submit(req("agent-x"), commit=lambda: committed.append(1),
                          assume_yes=True)
        self.assertTrue(out.committed)

    def test_default_when_no_policy_is_unchanged(self):
        gate = WriteGate()
        committed = []
        out = gate.submit(WriteRequest("config", "/x", "v"),
                          commit=lambda: committed.append(1), assume_yes=True)
        self.assertTrue(out.committed)


if __name__ == "__main__":
    unittest.main()
