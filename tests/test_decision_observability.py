"""Stage 49 — decision observability: "what you did and WHY".

Two parts:
  1. RATIONALE COVERAGE — every consequential decision records a human-readable reason in
     its ledger entry: spec-conflict (which spec/decision + where), self-healing resolution
     (old->new diff + why), the deviation gate (why + resolution), and gate blocks/passes.
  2. `mokata audit --why` — a bounded, read-only what+decision+why timeline of the run.

Inviolable: the timeline is read-only (writes nothing, bumps no counter), bounded (a tail),
local-first, degrade-clean (no ledger -> friendly empty).
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MOKATA_DIR
from mokata.cli import main
from mokata.config import Surface
from mokata.engine.spec import AcceptanceCriterion, Spec
from mokata.engine.spec_awareness import ChangeSet, guard_change
from mokata.govern import AuditLedger, WriteGate, WriteRequest
from mokata.govern.ledger import WHY_TIMELINE_TAIL, why_timeline
from mokata.memory import MemoryItem, MemoryStore


def run_cli(argv):
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with redirect_stdout(buf):
            rc = main(argv)
    finally:
        sys.stdin = old
    return rc, buf.getvalue()


def _repo(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _ledger(d):
    return AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))


# ---------------------------------------------------- Part 1: rationale coverage
class TestRationaleCoverage(unittest.TestCase):
    def test_spec_conflict_records_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger(d)
            specs = [Spec(title="Payments",
                          criteria=[AcceptanceCriterion("AC1",
                                    "process_payment must be idempotent")])]
            guard_change(ChangeSet(symbols=["process_payment"]), specs=specs, decisions=[],
                         layer=None, ledger=led, confirm=lambda _t: False)
            conflict = next(e for e in led.entries() if e["kind"] == "spec_conflict")
            self.assertIn("reason", conflict)
            self.assertIn("Payments", conflict["reason"])         # the real spec it affects
            self.assertIn("process_payment", conflict["reason"])  # where (the touched symbol)

    def test_deviation_records_why_and_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger(d)
            specs = [Spec(title="Payments",
                          criteria=[AcceptanceCriterion("AC1", "process_payment idempotent")])]
            guard_change(ChangeSet(symbols=["process_payment"]), specs=specs, decisions=[],
                         layer=None, ledger=led, confirm=lambda _t: False)
            devs = [e for e in led.entries() if e["kind"] == "deviation"]
            self.assertTrue(devs)
            self.assertTrue(all(e.get("why") for e in devs))      # every deviation has a WHY
            self.assertTrue(any(e.get("decision") == "declined" for e in devs))

    def test_self_healing_resolution_records_diff_and_why(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d, profile="full")
            store = MemoryStore.from_surface(surface)
            store.backend.put(MemoryItem.create("db.engine", "postgres", kind="decision",
                                                created_at="2026-01-01T00:00:00+00:00"))
            store.backend.put(MemoryItem.create("db.engine", "mysql", kind="decision",
                                                created_at="2026-02-01T00:00:00+00:00"))
            proposal = store.detect_issues()[0]
            store.apply_proposal(proposal, "reject")              # a resolution decision
            entry = next(e for e in _ledger(d).entries()
                         if e["kind"] == "healing_decision")
            self.assertEqual(entry["decision"], "reject")
            self.assertFalse(entry["changed"])                    # reject changes nothing
            self.assertIn("->", entry["diff"])                    # the old -> new diff
            self.assertTrue(entry["reason"])                      # the WHY (rationale)

    def test_write_gate_block_records_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger(d)
            WriteGate(ledger=led).submit(
                WriteRequest("config", "x.json", content="hi"),
                commit=lambda: None, confirm=lambda _t: False)   # human declines
            entry = next(e for e in led.entries() if e["kind"] == "write_gate")
            self.assertEqual(entry["decision"], "declined")
            self.assertTrue(entry["reason"])


# ---------------------------------------------------- Part 2: the why timeline
class TestWhyTimeline(unittest.TestCase):
    def _entries(self):
        return [
            {"seq": 1, "kind": "spec_conflict", "at": "t",
             "reason": "affects spec 'Payments' (via process_payment)", "phase": "develop"},
            {"seq": 2, "kind": "deviation", "at": "t", "decision": "declined",
             "target": "process_payment", "why": "the change affects a saved spec"},
            {"seq": 3, "kind": "healing_decision", "at": "t", "subject": "db.engine",
             "decision": "approve", "changed": True, "diff": "'postgres' -> 'mysql'",
             "reason": "the newer fact supersedes the older one"},
            {"seq": 4, "kind": "write_gate", "at": "t", "target": "app.py",
             "decision": "declined", "reason": "human declined"},
        ]

    def test_renders_what_decision_and_why(self):
        lines = why_timeline(self._entries())
        joined = "\n".join(lines)
        self.assertIn("spec-awareness", joined)
        self.assertIn("Payments", joined)
        self.assertIn("deviation gate", joined)
        self.assertIn("self-healing", joined)
        self.assertIn("why:", joined)
        self.assertIn("the newer fact supersedes", joined)

    def test_is_bounded_to_the_tail(self):
        entries = [{"seq": i, "kind": "phase", "at": "t", "summary": str(i)}
                   for i in range(1, 101)]
        lines = why_timeline(entries, tail=10)
        self.assertEqual(len(lines), 10)
        self.assertIn("#100", lines[-1])           # the most-recent entries
        self.assertNotIn("#90", "\n".join(lines))

    def test_default_tail_is_frugal(self):
        self.assertEqual(WHY_TIMELINE_TAIL, 50)
        entries = [{"seq": i, "kind": "phase", "at": "t"} for i in range(1, 200)]
        self.assertEqual(len(why_timeline(entries)), 50)


# ---------------------------------------------------- Part 2: the CLI surface
class TestAuditWhyCommand(unittest.TestCase):
    def test_cmd_audit_why_renders_timeline(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            led = _ledger(d)
            led.record("spec_conflict", phase="develop",
                       reason="affects spec 'Payments' (via process_payment)")
            led.record("write_gate", target="app.py", decision="declined",
                       reason="human declined")
            rc, out = run_cli(["audit", "--why", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("why", out.lower())
            self.assertIn("Payments", out)
            self.assertIn("human declined", out)

    def test_cmd_audit_why_is_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            led = _ledger(d)
            led.record("write_gate", target="x", decision="approved", reason="committed")
            before_entries = len(led.entries())
            before_reads = MemoryStore.from_surface(Surface.load(d)).stats.reads
            run_cli(["audit", "--why", "--path", d])
            self.assertEqual(len(_ledger(d).entries()), before_entries)   # appended nothing
            after_reads = MemoryStore.from_surface(Surface.load(d)).stats.reads
            self.assertEqual(after_reads, before_reads)                   # bumped no counter

    def test_cmd_audit_why_degrades_clean_with_no_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            _repo(d)
            rc, out = run_cli(["audit", "--why", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("empty", out.lower())


if __name__ == "__main__":
    unittest.main()
