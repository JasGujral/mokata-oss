"""Stage 54c — pipeline legibility & flow UX (a bundle of small, read-only touches).

Every run should read like a guided flow: the user always knows what just happened, why a
gate fired, the one thing to do next, and how far along the stage is. These tests pin the five
touches:

  1. a SHARED one-line gate-verdict renderer (`✓ <gate> passed (…)` / `✗ <gate> blocked — …`)
     applied consistently across the completeness / spec / deviation / write gates;
  2. why-blocked + how-to-unblock — every block names the SINGLE next action;
  3. stage recap + next-step nudge (`✓ <stage> done — …. Next: /mokata:<next>`), honest
     mechanism only (autocomplete / model-continuation; never Tab/pre-fill);
  4. one-key approve / edit / reject over an editable value, SAFE DEFAULT = no change — and a
     security (secret) block can NEVER be approved away (the WriteGate hard-block holds);
  5. in-stage progress counters surfaced from build_progress (`[3/7]`) / AC coverage.

Everything in 1–3 and 5 is read-only/derived + deterministic; 4 stays human-gated.
"""

import os
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import cli, legibility, progress
from mokata.config import Surface
from mokata.memory import CONTEXT, PERSISTENT, MemoryItem, MemoryStore
from mokata.govern import PipelineCheckpoint
from mokata.govern.gate import WriteGate, WriteRequest
from mokata.govern.deviation import DeviationGate, DeviationRequest
from mokata.engine import AcceptanceCriterion, Spec, TestRef, run_completeness_gate
from mokata.engine.spec_gate import check_spec_persisted
from mokata.prompt import GateResponse, read_approve_edit_reject  # noqa: F401

# A well-known AWS-key shaped fixture, assembled from fragments so the literal never appears
# in source (mokata's own secret-guard would otherwise block writing this test file).
FAKE_SECRET = "AKIA" + "IOSFODNN7" + "EXAMPLE"


def _spec():
    return Spec(title="login", criteria=[
        AcceptanceCriterion("AC-1", "log in"),
        AcceptanceCriterion("AC-2", "log out"),
    ])


def _repo(d):
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    return Surface.load(d)


# ============================================================= 1. gate-verdict legibility
class TestGateVerdict(unittest.TestCase):
    def test_pass_one_liner(self):
        line = legibility.gate_verdict("completeness", True, "all 2 ACs map to tests")
        self.assertEqual(line, "✓ completeness passed (all 2 ACs map to tests)")

    def test_block_one_liner(self):
        line = legibility.gate_verdict("completeness", False, "1 AC unmapped")
        self.assertEqual(line, "✗ completeness blocked — 1 AC unmapped")

    def test_block_with_action_adds_unblock_line(self):
        line = legibility.gate_verdict("completeness", False, "1 AC unmapped",
                                       action="write a test for it")
        self.assertIn("✗ completeness blocked — 1 AC unmapped", line)
        self.assertIn("→ to unblock: write a test for it", line)

    def test_ascii_mode(self):
        line = legibility.gate_verdict("completeness", True, "ok", ascii_only=True)
        self.assertTrue(line.startswith("[PASS]"))
        self.assertNotIn("✓", line)
        blocked = legibility.gate_verdict("completeness", False, "no", ascii_only=True)
        self.assertTrue(blocked.startswith("[BLOCK]"))

    # the SAME renderer over each gate's real result object — verdicts are not re-derived
    def test_consistent_over_completeness_result(self):
        res = run_completeness_gate(_spec(), [TestRef("t1", ["AC-1"])])   # AC-2 unmapped
        line = legibility.verdict(res)
        self.assertTrue(line.startswith("✗ completeness blocked — "))
        self.assertIn("→ to unblock:", line)            # touch 2 — actionable

    def test_consistent_over_completeness_pass(self):
        res = run_completeness_gate(_spec(), [TestRef("t1", ["AC-1"]), TestRef("t2", ["AC-2"])])
        line = legibility.verdict(res)
        self.assertTrue(line.startswith("✓ completeness passed ("))
        self.assertNotIn("to unblock", line)            # pass gets a one-liner too

    def test_consistent_over_spec_gate_result(self):
        res = check_spec_persisted(store=None)          # no spec -> blocked
        line = legibility.verdict(res)
        self.assertTrue(line.startswith("✗ spec-persisted blocked — "))

    def test_consistent_over_write_outcome_secret(self):
        gate = WriteGate()
        out = gate.submit(WriteRequest("memory", "m.json", content=FAKE_SECRET),
                          assume_yes=True)
        line = legibility.verdict(out)
        self.assertTrue(line.startswith("✗ write blocked — "))
        self.assertIn("→ to unblock:", line)
        self.assertIn("secret", line.lower())

    def test_consistent_over_deviation_outcome(self):
        gate = DeviationGate()
        out = gate.submit(DeviationRequest(what="swap lib", why="faster"),
                          confirm=lambda _: True)       # approved
        line = legibility.verdict(out)
        self.assertTrue(line.startswith("✓ deviation passed ("))


# ============================================================= 2. why-blocked / how-to-unblock
class TestUnblockHint(unittest.TestCase):
    def test_known_gates_have_an_action(self):
        for gid in ("completeness", "spec-persisted", "approach-approval",
                    "refinement-approval", "emit-approval", "red-before-green", "deviation"):
            self.assertTrue(legibility.unblock_hint(gid), f"{gid} has no unblock hint")

    def test_unknown_gate_degrades_to_none(self):
        self.assertIsNone(legibility.unblock_hint("no-such-gate"))

    def test_completeness_render_now_names_the_next_action(self):
        res = run_completeness_gate(_spec(), [TestRef("t1", ["AC-1"])])
        self.assertIn("to unblock", res.render().lower())   # render() gained the action line


# ============================================================= 3. stage recap + next-step nudge
class TestStageRecapAndNudge(unittest.TestCase):
    def test_next_command_follows_the_user_arc(self):
        self.assertEqual(legibility.next_command("brainstorm"), "/mokata:spec")
        self.assertEqual(legibility.next_command("spec"), "/mokata:develop")
        self.assertEqual(legibility.next_command("develop"), "/mokata:review")
        self.assertEqual(legibility.next_command("review"), "/mokata:ship")

    def test_terminal_stage_has_no_next(self):
        self.assertIsNone(legibility.next_command("ship"))

    def test_unknown_stage_degrades_to_none(self):
        self.assertIsNone(legibility.next_command("frobnicate"))

    def test_recap_includes_recap_and_next(self):
        line = legibility.stage_recap("spec", "5 ACs written")
        self.assertIn("✓ spec done — 5 ACs written", line)
        self.assertIn("Next: `/mokata:develop`", line)

    def test_recap_terminal_has_no_next(self):
        line = legibility.stage_recap("ship", "merged to main")
        self.assertIn("✓ ship done — merged to main", line)
        self.assertNotIn("Next:", line)

    def test_nudge_mechanism_is_honest_not_prefill(self):
        # the helper never claims to pre-fill the box or rebind Tab — it points at a command
        line = legibility.stage_recap("spec", "done")
        self.assertNotIn("Tab", line)
        self.assertNotIn("pre-fill", line.lower())


# ============================================================= 4. approve / edit / reject gate
class TestApproveEditReject(unittest.TestCase):
    def _reader(self, answers):
        it = iter(answers)
        return lambda _prompt="": next(it)

    def test_approve_keeps_proposed_value(self):
        r = read_approve_edit_reject("change X", "NEW", reader=self._reader(["a"]))
        self.assertEqual(r.action, "approve")
        self.assertEqual(r.value, "NEW")
        self.assertTrue(r.is_change)

    def test_edit_supplies_a_new_value(self):
        r = read_approve_edit_reject("change X", "NEW", reader=self._reader(["e", "EDITED"]))
        self.assertEqual(r.action, "edit")
        self.assertEqual(r.value, "EDITED")
        self.assertTrue(r.is_change)

    def test_reject_is_no_change(self):
        r = read_approve_edit_reject("change X", "NEW", reader=self._reader(["r"]))
        self.assertEqual(r.action, "reject")
        self.assertIsNone(r.value)
        self.assertFalse(r.is_change)

    def test_safe_default_blank_is_reject(self):
        r = read_approve_edit_reject("change X", "NEW", reader=self._reader([""]))
        self.assertEqual(r.action, "reject")
        self.assertFalse(r.is_change)

    def test_eof_is_reject(self):
        def boom(_prompt=""):
            raise EOFError
        r = read_approve_edit_reject("change X", "NEW", reader=boom)
        self.assertEqual(r.action, "reject")

    def test_approve_cannot_override_a_security_block(self):
        # touch 4 MUST NOT weaken security: a secret is a hard block the human-gate can't pass
        gate = WriteGate()
        out = gate.submit(WriteRequest("memory", "m.json", content=FAKE_SECRET),
                          confirm=lambda _prompt: True)   # "approve" everything
        self.assertFalse(out.committed)
        self.assertTrue(out.findings)                      # blocked by the secret scan


# ============================================================= 4b. approve/edit/reject WIRED
class TestApproveEditRejectWiredIntoMemoryEdit(unittest.TestCase):
    """`mokata memory edit` (no --yes) surfaces the old→new diff and reads approve/edit/reject;
    the SAFE DEFAULT (reject / EOF) makes NO change — the apply_proposal mechanism is reused."""

    def _seed(self, d):
        from mokata.init import init_repo
        init_repo(root=d, profile="full", assume_yes=True, out=lambda _: None)
        store = MemoryStore.from_surface(Surface.load(d))
        store.remember(MemoryItem.create("tax_rate", "0.2", mtype=PERSISTENT, kind=CONTEXT),
                       assume_yes=True)
        store.close()

    def _values(self, d):
        items = MemoryStore.from_surface(Surface.load(d)).backend.all()
        return {i.value: i for i in items if i.subject == "tax_rate"}

    def test_reject_makes_no_change(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed(d)
            with mock.patch("builtins.input", side_effect=["r"]):
                rc = cli.main(["memory", "edit", "tax_rate", "--value", "0.25", "--path", d])
            self.assertEqual(rc, 0)
            self.assertNotIn("0.25", self._values(d))        # not applied — safe default
            self.assertEqual(self._values(d)["0.2"].status, "active")

    def test_eof_default_makes_no_change(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed(d)
            with mock.patch("builtins.input", side_effect=EOFError):
                rc = cli.main(["memory", "edit", "tax_rate", "--value", "0.25", "--path", d])
            self.assertEqual(rc, 0)
            self.assertNotIn("0.25", self._values(d))        # EOF -> reject -> no change

    def test_approve_applies_the_change(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed(d)
            with mock.patch("builtins.input", side_effect=["a"]):
                rc = cli.main(["memory", "edit", "tax_rate", "--value", "0.25", "--path", d])
            self.assertEqual(rc, 0)
            vals = self._values(d)
            self.assertEqual(vals["0.25"].status, "active")  # approved -> applied
            self.assertEqual(vals["0.2"].status, "superseded")  # old superseded, not erased


# ============================================================= 5. in-stage progress counters
class TestCounters(unittest.TestCase):
    def test_counter_with_unit(self):
        self.assertEqual(legibility.counter(3, 7, "ACs"), "[3/7 ACs]")

    def test_counter_without_unit(self):
        self.assertEqual(legibility.counter(2, 5), "[2/5]")

    def test_stage_counter_from_build_progress(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").mark_passed("brainstorm")
            prog = progress.build_progress(surface.state)
            self.assertEqual(legibility.stage_counter(prog), "[1/7]")

    def test_stage_counter_no_run_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            prog = progress.build_progress(surface.state)
            self.assertEqual(legibility.stage_counter(prog), "")


# ============================================================= read-only / deterministic
class TestReadOnlyDeterministic(unittest.TestCase):
    def test_helpers_are_deterministic(self):
        a = legibility.gate_verdict("g", False, "r", action="do x")
        b = legibility.gate_verdict("g", False, "r", action="do x")
        self.assertEqual(a, b)
        self.assertEqual(legibility.stage_recap("spec", "x"),
                         legibility.stage_recap("spec", "x"))

    def test_verdict_does_not_write_state(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _repo(d)
            PipelineCheckpoint(surface.state, "run-a").mark_passed("brainstorm")
            before = sorted(os.listdir(surface.state.root))
            res = run_completeness_gate(_spec(), [TestRef("t1", ["AC-1"])])
            legibility.verdict(res)
            legibility.stage_counter(progress.build_progress(surface.state))
            self.assertEqual(before, sorted(os.listdir(surface.state.root)))


if __name__ == "__main__":
    unittest.main()
