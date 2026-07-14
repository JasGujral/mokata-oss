"""MS.S3 — ledger hash-chain + cross-process lock + O(1) seq (fixes B1/M-3 and B2).

The audit ledger is the product's honesty: every gate claim is only checkable because the ledger
is tamper-evident and correctly attributed. This suite pins:

  * M-3/B1 — two real OS processes appending concurrently produce 2N entries with strictly
    monotonic, UNIQUE seqs, valid JSONL, and a chain that verifies intact (no interleave/collision).
  * B2 — racing gated writes each carry THEIR OWN approval id (no misattribution); the seq
    WriteGate.submit returns propagates to every recorded id.
  * Tamper detection — editing/truncating a chained entry is named by verify at the first break;
    pre-chain (unhashed) legacy entries are reported as a boundary, not a failure.
  * O(1) honesty — assigning the next seq / reporting count does not re-read the whole ledger.
  * Crash self-heal — a lost/stale counter is rebuilt by scan with no duplicate/skipped seq.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

from mokata.govern.ledger import AuditLedger, verify_chain


# A tiny worker run as a REAL separate process: append N ledger entries to the shared file.
_WORKER = textwrap.dedent(
    """
    import sys
    from mokata.govern.ledger import AuditLedger
    path, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    led = AuditLedger(path)
    for i in range(n):
        led.record("worker", tag=tag, i=i)
    """
)


def _run_worker(path, tag, n):
    env = dict(os.environ)
    src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen([sys.executable, "-c", _WORKER, path, tag, str(n)], env=env)


class Test_M3_Concurrent(unittest.TestCase):
    def test_two_processes_append_concurrently(self):
        """B1/M-3: 2N entries, strictly-monotonic UNIQUE seqs, valid JSONL, chain intact."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "audit", "ledger.jsonl")
            n = 40
            p1 = _run_worker(path, "A", n)
            p2 = _run_worker(path, "B", n)
            self.assertEqual(p1.wait(timeout=60), 0)
            self.assertEqual(p2.wait(timeout=60), 0)

            seqs = []
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        seqs.append(json.loads(line)["seq"])   # valid JSONL, line by line

            self.assertEqual(len(seqs), 2 * n)                 # nothing dropped
            self.assertEqual(seqs, list(range(1, 2 * n + 1)))  # monotonic + unique + gapless

            report = AuditLedger(path).verify()
            self.assertTrue(report.intact, report.reason)
            self.assertEqual(report.checked, 2 * n)


class Test_B2_Attribution(unittest.TestCase):
    def _gate(self, path):
        from mokata.govern import WriteGate, WriteRequest
        return WriteGate(ledger=AuditLedger(path)), WriteRequest

    def test_submit_returns_the_real_assigned_seq(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            gate, WR = self._gate(path)
            out1 = gate.submit(WR("config", "/a", "x"), assume_yes=True)
            out2 = gate.submit(WR("config", "/b", "y"), assume_yes=True)
            entries = AuditLedger(path).entries()
            approved = [e["seq"] for e in entries if e.get("decision") == "approved"]
            self.assertEqual([out1.approval_seq, out2.approval_seq], approved)

    def test_predicted_id_equals_own_entry_under_commit(self):
        """A commit closure that predicts len+1 gets the SAME seq the gate's approved entry lands
        at — the predict window is held under the ledger lock, so it can't misattribute."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            led = AuditLedger(path)
            from mokata.govern import WriteGate, WriteRequest
            gate = WriteGate(ledger=led)
            predicted = {}

            def _commit():
                predicted["id"] = len(led) + 1     # the store's B2 prediction pattern

            out = gate.submit(WriteRequest("memory", "m:k", content="v"),
                              commit=_commit, assume_yes=True)
            self.assertEqual(predicted["id"], out.approval_seq)
            approved = [e for e in led.entries() if e.get("decision") == "approved"]
            self.assertEqual(approved[0]["seq"], out.approval_seq)


class Test_Tamper(unittest.TestCase):
    def _seed(self, path, n=5):
        led = AuditLedger(path)
        for i in range(n):
            led.record("event", i=i)
        return led

    def test_edited_payload_names_first_broken_seq(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            self._seed(path, 5)
            lines = open(path, encoding="utf-8").read().splitlines()
            rec = json.loads(lines[2])           # tamper with seq 3's payload
            rec["i"] = 999
            lines[2] = json.dumps(rec)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            report = AuditLedger(path).verify()
            self.assertFalse(report.intact)
            self.assertEqual(report.first_break_seq, 3)

    def test_truncated_tail_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            self._seed(path, 6)
            lines = open(path, encoding="utf-8").read().splitlines()
            with open(path, "w", encoding="utf-8") as fh:      # drop the last 2 entries
                fh.write("\n".join(lines[:-2]) + "\n")
            report = AuditLedger(path).verify()
            self.assertFalse(report.intact)

    def test_pre_chain_entries_reported_as_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:      # legacy: NO hash fields
                fh.write(json.dumps({"seq": 1, "kind": "old", "at": "t"}) + "\n")
                fh.write(json.dumps({"seq": 2, "kind": "old", "at": "t"}) + "\n")
            led = AuditLedger(path)
            led.record("new", i=0)               # chain starts here
            led.record("new", i=1)
            report = led.verify()
            self.assertTrue(report.intact, report.reason)      # old entries are NOT a failure
            self.assertEqual(report.pre_chain_count, 2)
            self.assertEqual(report.chain_start_seq, 3)

    def test_verify_chain_pure_function_intact(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            self._seed(path, 4)
            report = verify_chain(AuditLedger(path).entries())
            self.assertTrue(report.intact)
            self.assertEqual(report.checked, 4)


class Test_O1_SelfHeal(unittest.TestCase):
    def test_count_does_not_reread_whole_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            led = AuditLedger(path)
            for i in range(50):
                led.record("event", i=i)

            import builtins
            real_open = builtins.open
            reads = {"n": 0}

            def _spy(file, mode="r", *a, **k):
                if (os.path.abspath(str(file)) == os.path.abspath(path)
                        and "r" in mode and "w" not in mode and "a" not in mode):
                    reads["n"] += 1
                return real_open(file, mode, *a, **k)

            builtins.open = _spy
            try:
                self.assertEqual(len(led), 50)         # count: O(1)
                led.record("event", i=99)              # assign next seq: O(1)
            finally:
                builtins.open = real_open
            self.assertEqual(reads["n"], 0)            # never re-read the full ledger

    def test_counter_loss_rebuilds_by_scan(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            led = AuditLedger(path)
            for i in range(5):
                led.record("event", i=i)
            for f in os.listdir(os.path.dirname(path)):       # blow away the O(1) counter sidecar
                if f.endswith(".count"):
                    os.remove(os.path.join(os.path.dirname(path), f))
            led2 = AuditLedger(path)
            self.assertEqual(len(led2), 5)             # rebuilt count
            e = led2.record("event", i=5)
            self.assertEqual(e["seq"], 6)              # no duplicate / skipped seq
            self.assertTrue(led2.verify().intact)      # chain still verifies

    def test_stale_counter_after_external_append_self_heals(self):
        """Crash between append and counter-update: file is ahead of the counter -> next append
        must not duplicate/skip a seq (rebuild-by-scan on size mismatch)."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            led = AuditLedger(path)
            e2 = led.record("event", i=0)
            led.record("event", i=1)
            from mokata.govern.ledger import _entry_hash
            rogue = {"seq": 3, "kind": "event", "at": "t", "i": 2,
                     "prev_hash": led.entries()[-1]["entry_hash"]}
            rogue["entry_hash"] = _entry_hash(rogue)
            with open(path, "a", encoding="utf-8") as fh:      # crash-appended, counter never learned
                fh.write(json.dumps(rogue) + "\n")
            e = led.record("event", i=3)
            self.assertEqual(e["seq"], 4)              # lands at 4, not colliding at 3
            self.assertEqual([x["seq"] for x in led.entries()], [1, 2, 3, 4])
            self.assertTrue(led.verify().intact)
            self.assertNotEqual(e2["seq"], e["seq"])


if __name__ == "__main__":
    unittest.main()
