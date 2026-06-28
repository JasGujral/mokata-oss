"""I1/I2/I3, G4, E1 — secret protection, human-gated writes, audit ledger, sync/async
hooks, and RED-before-GREEN enforcement."""

import os
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.govern import (
    AuditLedger,
    RedBeforeGreenError,
    TddGuard,
    WriteGate,
    WriteRequest,
    has_secrets,
    run_async_hook,
    run_sync_hook,
    scan,
)


# --- I1: 4-layer secret protection ----------------------------------------------
class TestSecretProtection(unittest.TestCase):
    def test_layer1_signature(self):
        findings = scan(text="aws = 'AKIAIOSFODNN7EXAMPLE'")
        self.assertTrue(any(f.layer == "signature" for f in findings))

    def test_layer2_entropy(self):
        findings = scan(text="token = a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0")
        self.assertTrue(any(f.layer == "entropy" for f in findings))

    def test_layer3_sensitive_path(self):
        findings = scan(text="x=1", path="/repo/.env")
        self.assertTrue(any(f.layer == "path" for f in findings))

    def test_layer4_egress_blocks_sending_a_secret(self):
        findings = scan(text="AKIAIOSFODNN7EXAMPLE", for_send=True)
        self.assertTrue(any(f.layer == "egress" for f in findings))

    # --- entropy-layer precision: paths / URLs / UUIDs in content are NOT secrets
    #     (regression: a long file path was flagged, blocking legitimate doc/code writes) ---

    def _entropy(self, s):
        return any(f.layer == "entropy" for f in scan(text=s))

    def test_entropy_ignores_file_paths_and_urls(self):
        for benign in (
            "docs/build/02-mokata-build-status.md",
            "/Users/x/Documents/Development/claude/cowork/mokata/docs/build/02-mokata-build-status.md",
            "https://github.com/JasGujral/mokata-oss/releases/tag/v0.0.2",
            "src/mokata/govern/secrets.py",
            "550e8400-e29b-41d4-a716-446655440000",        # UUID
            "02-mokata-build-status-final-draft",            # lowercase kebab slug
        ):
            self.assertFalse(self._entropy(benign), f"false positive on {benign!r}")

    def test_entropy_still_catches_real_secrets(self):
        for secret in (
            "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",              # contiguous hex key
            "dGhpc2lzYXNlY3JldGtleTEyMzQ1Ng",                 # base64 blob
            "aB3-xY9z-kQ2m-NpL7-wRtV-bGcH-dEf1",              # mixed-case base64url
        ):
            self.assertTrue(self._entropy(secret), f"missed secret {secret!r}")

    def test_clean_content_has_no_findings(self):
        self.assertFalse(has_secrets(scan(text="the quick brown fox", path="/a/b.py")))


# --- I3: audit ledger ------------------------------------------------------------
class TestAuditLedger(unittest.TestCase):
    def test_append_only_with_increasing_seq(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "ledger.jsonl"))
            led.record("gate", decision="approve", target="spec")
            led.record("tool_call", tool="grep")
            entries = led.entries()
            self.assertEqual([e["seq"] for e in entries], [1, 2])
            self.assertEqual(entries[0]["kind"], "gate")

    def test_persists_to_disk_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            AuditLedger(path).record("write", target="x")
            self.assertEqual(len(AuditLedger(path).entries()), 1)


# --- I2: universal human-gated writes -------------------------------------------
class TestWriteGate(unittest.TestCase):
    def _gate(self, d):
        return WriteGate(ledger=AuditLedger(os.path.join(d, "ledger.jsonl")))

    def test_refused_without_approval(self):
        with tempfile.TemporaryDirectory() as d:
            written = []
            out = self._gate(d).submit(
                WriteRequest("config", "/repo/x.cfg", "value=1"),
                commit=lambda: written.append(1),
                confirm=lambda _t: False,
            )
            self.assertFalse(out.committed)
            self.assertTrue(out.aborted)
            self.assertEqual(written, [])      # commit never ran

    def test_commits_after_approval(self):
        with tempfile.TemporaryDirectory() as d:
            written = []
            out = self._gate(d).submit(
                WriteRequest("config", "/repo/x.cfg", "value=1"),
                commit=lambda: written.append(1),
                assume_yes=True,
            )
            self.assertTrue(out.committed)
            self.assertEqual(written, [1])

    def test_secret_blocks_write_even_with_approval(self):
        with tempfile.TemporaryDirectory() as d:
            written = []
            out = self._gate(d).submit(
                WriteRequest("code", "/repo/app.py", "KEY='AKIAIOSFODNN7EXAMPLE'"),
                commit=lambda: written.append(1),
                assume_yes=True,                # human approved, but a secret is present
            )
            self.assertFalse(out.committed)
            self.assertTrue(out.findings)
            self.assertEqual(written, [])

    def test_gate_decisions_land_in_the_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "ledger.jsonl"))
            gate = WriteGate(ledger=led)
            gate.submit(WriteRequest("config", "/r/a", "ok"), assume_yes=True)
            gate.submit(WriteRequest("config", "/r/b", "x"), confirm=lambda _t: False)
            kinds = [e["kind"] for e in led.entries()]
            self.assertTrue(all(k == "write_gate" for k in kinds))
            self.assertEqual(len(kinds), 2)


# --- G4: sync/async hooks --------------------------------------------------------
class TestHooks(unittest.TestCase):
    def test_security_sync_hook_blocks_with_exit_2(self):
        res = run_sync_hook("secret-guard", passed=False, reason="secret found")
        self.assertTrue(res.blocked)
        self.assertEqual(res.exit_code, 2)

    def test_security_sync_hook_passes_with_exit_0(self):
        res = run_sync_hook("secret-guard", passed=True)
        self.assertFalse(res.blocked)
        self.assertEqual(res.exit_code, 0)

    def test_sync_hook_must_be_security(self):
        with self.assertRaises(ValueError):
            run_sync_hook("noisy", passed=False, security=False)

    def test_async_hook_never_blocks_even_on_error(self):
        def boom():
            raise RuntimeError("observer failed")
        res = run_async_hook("metrics", boom)
        self.assertFalse(res.blocked)
        self.assertEqual(res.exit_code, 0)


# --- E1: RED before GREEN --------------------------------------------------------
class TestRedBeforeGreen(unittest.TestCase):
    def test_implementation_blocked_before_a_failing_test(self):
        guard = TddGuard()
        with self.assertRaises(RedBeforeGreenError):
            guard.guard_implementation("test_login")

    def test_implementation_allowed_after_red(self):
        guard = TddGuard()
        guard.record_red("test_login")          # the test ran and FAILED
        guard.guard_implementation("test_login")  # now allowed, no raise
        self.assertTrue(guard.allow_implementation("test_login"))


if __name__ == "__main__":
    unittest.main()
