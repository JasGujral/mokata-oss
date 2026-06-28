"""Stage 42 — safety-governance WIRING (I4 lethal-trifecta + G3 Karpathy + G5 learning).

These modules are unit-tested elsewhere; this file proves they are now CALLED from real
runtime paths (the "dark code" gap 0.0.3 closes):

  - I4: TrifectaGuard.gate_outbound runs on the outbound/publish path so publishing
        PRIVATE data is human-gated + logged; clean content is not gated (degrade-clean).
  - G3: run_karpathy_for_phase fires per phase inside the engine pipeline, toggleable
        per gate + audited; skipped (and not audited) when off / no manifest.
  - G5: a recurring correction in the ledger yields a human-gated promotion PROPOSAL
        (proposal-only — never an auto-write), surfaced via `mokata rules`.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata import MOKATA_DIR
from mokata.brainstorm import PIPELINE_PHASES, Approach, BrainstormSession
from mokata.cli import main
from mokata.engine import AcceptanceCriterion, Spec, TestRef, run_pipeline
from mokata.govern import (
    AuditLedger,
    OutboundRequest,
    RulePromotion,
    TrifectaGuard,
    gate_outbound_publish,
    learn_from_ledger,
    looks_private,
)
from mokata.init import init_repo
from mokata.manifest import Manifest
from mokata.profiles import build_manifest_data
from mokata.state import StateStore


def silent(_):
    pass


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


def handoff():
    s = BrainstormSession("slugify")
    s.ask("unicode or ascii-only?")
    s.answer("ascii-only")
    s.propose_approaches([
        Approach("regex", "strip via regex", pros=["tiny"], cons=["unicode edge cases"]),
        Approach("library", "use a slug lib", pros=["robust"], cons=["dependency"]),
    ])
    s.approve("jas", "regex")
    return s.handoff()


def spec2():
    return Spec("slugify", [AcceptanceCriterion("AC-1", "lowercase + hyphenate"),
                            AcceptanceCriterion("AC-2", "strip punctuation")])


FULL_TESTS = [TestRef("t1", ["AC-1"]), TestRef("t2", ["AC-2"])]


def make_manifest(overrides=None):
    data = build_manifest_data("standard", "0.0.3")
    if overrides:
        gov = data.setdefault("settings", {}).setdefault("governance", {})
        gov["karpathy"] = overrides
    return Manifest.from_dict(data)


# --- G3: Karpathy gates fire per phase inside the engine pipeline ---------------
class TestKarpathyPipelineWiring(unittest.TestCase):
    def test_gates_fire_per_phase_and_are_audited_when_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            run = run_pipeline(handoff(), spec2(), FULL_TESTS,
                               store=StateStore(os.path.join(d, "state")),
                               ledger=led, manifest=make_manifest())
            fired = {e["gate"] for e in led.entries() if e["kind"] == "karpathy_gate"}
            self.assertEqual(fired,
                             {"think-first", "simplicity", "surgical-scope", "verify"})
            self.assertTrue(run.karpathy)              # surfaced on the run

    def test_skipped_and_not_audited_without_a_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            run = run_pipeline(handoff(), spec2(), FULL_TESTS,
                               store=StateStore(os.path.join(d, "state")), ledger=led)
            self.assertEqual(
                [e for e in led.entries() if e["kind"] == "karpathy_gate"], [])
            self.assertEqual(run.karpathy, [])         # degrade-clean

    def test_a_disabled_gate_does_not_fire_or_audit(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            run_pipeline(handoff(), spec2(), FULL_TESTS,
                         store=StateStore(os.path.join(d, "state")), ledger=led,
                         manifest=make_manifest(overrides={"think-first": False}))
            fired = {e["gate"] for e in led.entries() if e["kind"] == "karpathy_gate"}
            self.assertNotIn("think-first", fired)     # toggled off
            self.assertIn("simplicity", fired)         # the others still fire

    def test_phases_still_logged_normally_no_regression(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            run_pipeline(handoff(), spec2(), FULL_TESTS,
                         store=StateStore(os.path.join(d, "state")), ledger=led,
                         manifest=make_manifest())
            phases = [e["phase"] for e in led.entries() if e["kind"] == "phase"]
            self.assertEqual(phases, list(PIPELINE_PHASES))


# --- I4: lethal-trifecta gate on the outbound/publish path ----------------------
class TestOutboundTrifectaWiring(unittest.TestCase):
    def test_looks_private_flags_private_content_not_clean(self):
        self.assertTrue(looks_private("This doc is CONFIDENTIAL — internal only."))
        self.assertFalse(looks_private("# Slugify spec\nlowercase and hyphenate."))

    def test_private_outbound_is_gated_and_logged_on_decline(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            dec = gate_outbound_publish(
                OutboundRequest("vault-push", "team-vault", payload="CONFIDENTIAL"),
                private_data=True, ledger=led, confirm=lambda _t: False)
            self.assertFalse(dec.allowed)
            self.assertTrue(dec.gated)
            ob = [e for e in led.entries() if e["kind"] == "outbound"]
            self.assertEqual(len(ob), 1)
            self.assertTrue(ob[0]["trifecta"])
            self.assertFalse(ob[0]["allowed"])

    def test_private_outbound_proceeds_on_human_approval(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            dec = gate_outbound_publish(
                OutboundRequest("vault-push", "team-vault", payload="CONFIDENTIAL"),
                private_data=True, ledger=led, confirm=lambda _t: True)
            self.assertTrue(dec.allowed)
            self.assertTrue(dec.gated)

    def test_non_private_outbound_is_not_gated(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            dec = gate_outbound_publish(
                OutboundRequest("vault-push", "team-vault", payload="clean design doc"),
                private_data=False, ledger=led, confirm=lambda _t: False)
            self.assertTrue(dec.allowed)
            self.assertFalse(dec.gated)
            ob = [e for e in led.entries() if e["kind"] == "outbound"]
            self.assertFalse(ob[0]["trifecta"])

    def test_vault_push_of_private_content_is_trifecta_gated(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            src = os.path.join(d, "design.md")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("# Plan\nCONFIDENTIAL — internal only.\nApproach: regex.")
            rc, _ = run_cli(["vault", "push", "plan", src, "--yes", "--path", d])
            led = AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))
            ob = [e for e in led.entries() if e["kind"] == "outbound"]
            self.assertTrue(ob and ob[0]["trifecta"])  # the publish hit the trifecta gate

    def test_vault_push_of_clean_content_is_not_trifecta_gated(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            src = os.path.join(d, "spec.md")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("# Slugify spec\nlowercase + hyphenate, strip punctuation.")
            rc, _ = run_cli(["vault", "push", "spec", src, "--yes", "--path", d])
            self.assertEqual(rc, 0)
            led = AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))
            self.assertEqual(
                [e for e in led.entries() if e["kind"] == "outbound"], [])  # never gated


# --- G5: rules-learning proposes (only) from recurring ledger corrections -------
class TestRulesLearningFromLedger(unittest.TestCase):
    def test_recurring_correction_yields_a_proposal(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            for _ in range(3):
                led.record("write_gate", write_kind="code", target="src/x.py",
                           actor="cli", decision="declined", reason="risky")
            proposals = learn_from_ledger(led, threshold=3)
            self.assertEqual(len(proposals), 1)
            self.assertIsInstance(proposals[0], RulePromotion)
            self.assertGreaterEqual(proposals[0].occurrences, 3)

    def test_below_threshold_proposes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            for _ in range(2):
                led.record("revert", target="config:x")
            self.assertEqual(learn_from_ledger(led, threshold=3), [])

    def test_is_proposal_only_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            for _ in range(3):
                led.record("revert", target="config:x")
            before = led.entries()
            proposals = learn_from_ledger(led, threshold=3)
            self.assertTrue(proposals)
            self.assertEqual(led.entries(), before)    # pure read — nothing promoted/written

    def test_cmd_rules_surfaces_proposals_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            led = AuditLedger.from_mokata_dir(os.path.join(d, MOKATA_DIR))
            for _ in range(3):
                led.record("revert", target="config:risky-thing")
            rc, out = run_cli(["rules", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("proposal", out.lower())

    def test_cmd_rules_clean_ledger_shows_no_proposals(self):
        with tempfile.TemporaryDirectory() as d:
            init_repo(root=d, profile="standard", assume_yes=True, out=silent)
            rc, out = run_cli(["rules", "--path", d])
            self.assertEqual(rc, 0)
            self.assertNotIn("proposal", out.lower())


if __name__ == "__main__":
    unittest.main()
