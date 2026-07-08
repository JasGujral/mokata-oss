"""TM.S8 — hard-rule enforcement IN-RUN (doc 62 §4, Sentinel model).

This is where enforcement actually FIRES (S6 gathers the in-scope union, S7 binds the level):
  * advisory  → surfaced/flagged, the run PROCEEDS (no block);
  * soft      → BLOCKS, but an explicit override is allowed and LEDGERED (non-repudiation);
  * hard      → BLOCKS with NO runtime override (only removal/supersede, a gated write);
  * fail-closed on doubt — an undeterminable enforcement is treated as blocking.

Most-restrictive-wins at any depth (S6 safety class): a hard rule anywhere on the path is
authoritative; a narrower scope cannot loosen it. Enforcement of EXISTING rules only — no new
authoring. Local/personal mode: a rule fires only if present; zero-config is unaffected.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.govern import AuditLedger
from mokata.govern.enforce import (
    RULE_BLOCK_KIND,
    RULE_OVERRIDE_KIND,
    EnforcementGate,
    PendingAction,
    evaluate,
    in_scope_rules,
    render_verdict,
)
from mokata.govern.gate import WriteGate, WriteRequest
from mokata.memory.item import ADVISORY, GUARDRAIL, HARD, RULE, SOFT, MemoryItem
from mokata.memory.scope import ScopeContext


def _rule(subject, enforcement="", *, kind=RULE, level="personal", sid="", value="do not",
          id=None):
    return MemoryItem.create(subject, value, kind=kind, enforcement=enforcement,
                             scope_level=level, scope_id=sid, id=id)


def _action(*violates, kind="code", target="src/x.py"):
    return PendingAction(kind=kind, target=target, violates=frozenset(violates))


# ============================================================ gather: union (S6) × kind (S7)
class TestInScopeRules(unittest.TestCase):
    def test_filters_to_governance_rules_only(self):
        items = [_rule("r1", HARD), _rule("g1", kind=GUARDRAIL),
                 MemoryItem.create("db", "postgres")]         # a fact, not a rule
        got = in_scope_rules(items, context=None)             # local: no scope filter
        self.assertEqual({r.subject for r in got}, {"r1", "g1"})

    def test_union_read_limits_to_the_scope_path(self):
        team = _rule("team-rule", HARD, level="team", sid="acme")
        other = _rule("other-team", HARD, level="team", sid="globex")   # off-path
        personal = _rule("mine", SOFT)                                   # personal, on-path
        ctx = ScopeContext(team="acme", project="web")
        got = {r.subject for r in in_scope_rules([team, other, personal], context=ctx)}
        self.assertEqual(got, {"team-rule", "mine"})


# ============================================================ the enforcement decision
class TestEvaluate(unittest.TestCase):
    def test_hard_violation_blocks_and_is_not_overridable(self):
        v = evaluate(_action("no-force-push"),
                     [_rule("no-force-push", HARD, value="never force-push main")])
        self.assertTrue(v.blocked)
        self.assertFalse(v.overridable)                # hard has no runtime override
        self.assertEqual([x.subject for x in v.hard], ["no-force-push"])

    def test_soft_violation_blocks_but_is_overridable(self):
        v = evaluate(_action("prefer-pathlib"), [_rule("prefer-pathlib", SOFT)])
        self.assertTrue(v.blocked)
        self.assertTrue(v.overridable)
        self.assertEqual([x.subject for x in v.soft], ["prefer-pathlib"])

    def test_advisory_violation_only_flags_and_does_not_block(self):
        v = evaluate(_action("style-guide"), [_rule("style-guide", ADVISORY)])
        self.assertFalse(v.blocked)
        self.assertEqual([x.subject for x in v.flags], ["style-guide"])

    def test_no_in_scope_rule_no_block(self):
        # zero-config / local with no rules, OR an action that trips nothing → clean pass.
        self.assertFalse(evaluate(_action("anything"), []).blocked)
        self.assertFalse(evaluate(_action(), [_rule("r", HARD)]).blocked)   # nothing violated

    def test_most_restrictive_wins_hard_at_broader_scope_blocks(self):
        # a HARD rule at the broader (team) scope blocks even though the narrower (personal)
        # scope carries no rule for it — a narrower scope cannot loosen a hard rule.
        broad_hard = _rule("no-plaintext-secrets", HARD, level="team", sid="acme",
                           value="never store secrets in code")
        ctx = ScopeContext(team="acme", project="web")
        v = evaluate(_action("no-plaintext-secrets"), [broad_hard], context=ctx)
        self.assertTrue(v.blocked)
        self.assertFalse(v.overridable)
        self.assertEqual(v.hard[0].scope_level, "team")

    def test_hard_dominates_a_same_subject_narrower_advisory(self):
        # broad hard + narrow advisory on the SAME subject → hard wins (deny-overrides).
        broad = _rule("x", HARD, level="team", sid="acme")
        narrow = _rule("x", ADVISORY, level="personal")
        ctx = ScopeContext(team="acme")
        v = evaluate(_action("x"), [broad, narrow], context=ctx)
        self.assertTrue(v.blocked)
        self.assertFalse(v.overridable)               # the narrower advisory cannot loosen it

    def test_fail_closed_on_undeterminable_enforcement(self):
        class _Corrupt:
            id, subject, kind = "b1", "broken", RULE
            scope_level, scope_id, value = "personal", "", "?"

            @property
            def enforcement(self):
                raise RuntimeError("corrupt enforcement binding")

        v = evaluate(_action("broken"), [_Corrupt()])
        self.assertTrue(v.blocked)
        self.assertFalse(v.overridable)               # doubt → treated as a hard block
        self.assertEqual([x.subject for x in v.fail_closed], ["broken"])


# ============================================================ legible verdict (reuse gate_verdict)
class TestRenderVerdict(unittest.TestCase):
    def test_block_names_rule_scope_and_why(self):
        v = evaluate(_action("no-force-push"),
                     [_rule("no-force-push", HARD, level="team", sid="acme",
                            value="never force-push main")])
        out = render_verdict(v, ascii_only=True)
        self.assertIn("[BLOCK]", out)                 # the legible-gate format
        self.assertIn("no-force-push", out)           # names the rule
        self.assertIn("team", out)                    # names its scope
        self.assertIn("never force-push main", out)   # why it fired

    def test_pass_reads_as_a_legible_pass(self):
        out = render_verdict(evaluate(_action(), []), ascii_only=True)
        self.assertIn("[PASS]", out)


# ============================================================ the gate: block / override / ledger
class TestEnforcementGate(unittest.TestCase):
    def _led(self, d):
        return AuditLedger(os.path.join(d, "ledger.jsonl"))

    def test_hard_block_cannot_be_overridden_at_runtime(self):
        with tempfile.TemporaryDirectory() as d:
            led = self._led(d)
            gate = EnforcementGate(ledger=led)
            out = gate.check(_action("no-force-push"),
                             [_rule("no-force-push", HARD)],
                             override=lambda _v: True,      # try to override — must be ignored
                             actor="alice")
            self.assertFalse(out.allowed)
            self.assertFalse(out.overridden)
            # a hard block is recorded, and NO override entry exists (non-repudiation).
            self.assertTrue(any(e["kind"] == RULE_BLOCK_KIND for e in led.entries()))
            self.assertEqual([e for e in led.entries()
                              if e["kind"] == RULE_OVERRIDE_KIND], [])

    def test_soft_blocks_without_an_override(self):
        with tempfile.TemporaryDirectory() as d:
            led = self._led(d)
            out = EnforcementGate(ledger=led).check(_action("soft-rule"),
                                                    [_rule("soft-rule", SOFT)])
            self.assertFalse(out.allowed)              # blocks by default
            self.assertEqual([e for e in led.entries()
                              if e["kind"] == RULE_OVERRIDE_KIND], [])   # not ledgered

    def test_soft_explicit_override_proceeds_and_is_ledgered(self):
        with tempfile.TemporaryDirectory() as d:
            led = self._led(d)
            gate = EnforcementGate(ledger=led)
            out = gate.check(_action("prefer-pathlib"),
                             [_rule("prefer-pathlib", SOFT, value="use pathlib not os.path",
                                    id="pp1")],
                             override=lambda _v: True, actor="alice", reason="legacy shim")
            self.assertTrue(out.allowed)
            self.assertTrue(out.overridden)
            recs = [e for e in led.entries() if e["kind"] == RULE_OVERRIDE_KIND]
            self.assertEqual(len(recs), 1)
            e = recs[0]
            self.assertEqual(e["actor"], "alice")            # who
            self.assertEqual(e["item_id"], "pp1")            # which rule
            self.assertEqual(e["subject"], "prefer-pathlib")
            self.assertEqual(e["enforcement"], SOFT)
            self.assertEqual(e["reason"], "legacy shim")     # why (non-repudiation)

    def test_advisory_proceeds_with_flags_no_block_no_override(self):
        with tempfile.TemporaryDirectory() as d:
            led = self._led(d)
            out = EnforcementGate(ledger=led).check(_action("style"),
                                                    [_rule("style", ADVISORY)])
            self.assertTrue(out.allowed)
            self.assertFalse(out.overridden)
            self.assertEqual([f.subject for f in out.verdict.flags], ["style"])
            self.assertEqual(led.entries(), [])              # a flag ledgers nothing

    def test_clean_pass_when_nothing_in_scope(self):
        with tempfile.TemporaryDirectory() as d:
            out = EnforcementGate(ledger=self._led(d)).check(_action("x"), [])
            self.assertTrue(out.allowed)
            self.assertFalse(out.verdict.blocked)

    def test_override_surfaces_in_the_audit_why_timeline(self):
        from mokata.govern.ledger import why_timeline
        with tempfile.TemporaryDirectory() as d:
            led = self._led(d)
            EnforcementGate(ledger=led).check(
                _action("prefer-pathlib"), [_rule("prefer-pathlib", SOFT, id="pp1")],
                override=lambda _v: True, actor="bob", reason="one-off")
            lines = why_timeline(led.entries())
            self.assertTrue(any("prefer-pathlib" in ln and "one-off" in ln for ln in lines),
                            msg=lines)


# ============================================================ P14 — fires at the WriteGate
class TestWriteGateEnforcement(unittest.TestCase):
    def _led(self, d):
        return AuditLedger(os.path.join(d, "ledger.jsonl"))

    def test_hard_rule_blocks_the_write_at_the_gate(self):
        with tempfile.TemporaryDirectory() as d:
            committed = []
            gate = WriteGate(ledger=self._led(d))
            out = gate.submit(
                WriteRequest("code", "src/x.py", content="clean"),
                commit=lambda: committed.append(1), assume_yes=True,   # human-approved write …
                rules=[_rule("no-eval", HARD, value="never call eval")],
                action=_action("no-eval"))                             # … but a hard rule fires
            self.assertFalse(out.committed)
            self.assertTrue(out.aborted)
            self.assertEqual(committed, [])                            # never written
            self.assertIn("no-eval", out.reason)

    def test_soft_rule_override_lets_the_write_proceed_and_ledgers_it(self):
        with tempfile.TemporaryDirectory() as d:
            committed = []
            led = self._led(d)
            out = WriteGate(ledger=led).submit(
                WriteRequest("code", "src/x.py", content="clean"),
                commit=lambda: committed.append(1), assume_yes=True,
                rules=[_rule("prefer-pathlib", SOFT, id="pp1")],
                action=_action("prefer-pathlib"),
                override=lambda _v: True, actor="alice")
            self.assertTrue(out.committed)
            self.assertEqual(committed, [1])
            self.assertTrue(any(e["kind"] == RULE_OVERRIDE_KIND for e in led.entries()))

    def test_no_rules_writes_normally_local_unaffected(self):
        with tempfile.TemporaryDirectory() as d:
            committed = []
            out = WriteGate(ledger=self._led(d)).submit(
                WriteRequest("code", "src/x.py", content="clean"),
                commit=lambda: committed.append(1), assume_yes=True)   # no rules/action given
            self.assertTrue(out.committed)
            self.assertEqual(committed, [1])


if __name__ == "__main__":
    unittest.main()
