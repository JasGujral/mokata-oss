"""TM.S7 — typed items + enforcement binding (doc 62 §2 axis B, §4).

Covers the grooming decision exactly:
  * KIND ∈ {fact, rule, formula} as a normalized view over the Stage-36 kind field
    (RULE/GUARDRAIL→rule, formula reserved, everything else→fact);
  * ENFORCEMENT ∈ {advisory, soft, hard} as a SEPARATE binding on the item (Sentinel model),
    stored in the item JSON (no DB column), meaningful only for a rule;
  * NEW RULES BORN ADVISORY (ungated); mapped GUARDRAILs read as hard; legacy→advisory;
  * PROMOTION is the human-gated + ledgered moment — binding-only, no rewrite; demotion gated too;
    a declined gate changes/writes nothing;
  * precedence_class = SAFETY iff a rule's effective enforcement is hard.

No live DB — the local SQLite floor + an in-memory ledger cover everything.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.govern import AuditLedger
from mokata.memory import precedence as P
from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import (
    ADVISORY, FACT, FORMULA, GUARDRAIL, HARD, RULE, SOFT, ENFORCEMENT_LEVELS,
    MemoryItem, default_enforcement, effective_enforcement, governance_kind,
)
from mokata.memory.store import MemoryStore, PromoteResult


def _store(tmp):
    led = AuditLedger(os.path.join(tmp, "ledger.jsonl"))
    store = MemoryStore(SQLiteBackend(os.path.join(tmp, "m.db")), ledger=led)
    return store, led


# ============================================================ kind normalization (axis B)
class TestGovernanceKind(unittest.TestCase):
    def test_rule_and_guardrail_map_to_rule(self):
        self.assertEqual(governance_kind(RULE), RULE)
        self.assertEqual(governance_kind(GUARDRAIL), RULE)

    def test_formula_reserved_everything_else_is_fact(self):
        self.assertEqual(governance_kind(FORMULA), FORMULA)
        for kind in ("best-practice", "context", "reference", "decision", "persistent", ""):
            self.assertEqual(governance_kind(kind), FACT)

    def test_item_exposes_governance_kind(self):
        self.assertEqual(MemoryItem.create("k", "v", kind=RULE).governance_kind, RULE)
        self.assertEqual(MemoryItem.create("k", "v").governance_kind, FACT)


# ============================================================ enforcement round-trip + defaults
class TestEnforcementRoundTrip(unittest.TestCase):
    def test_new_rule_is_born_advisory(self):
        r = MemoryItem.create("no-force-push", "never force-push main", kind=RULE)
        self.assertEqual(r.enforcement, "")                 # unset on the record …
        self.assertEqual(r.effective_enforcement, ADVISORY)  # … derives advisory

    def test_mapped_guardrail_is_hard(self):
        g = MemoryItem.create("scan", "scan for secrets", kind=GUARDRAIL)
        self.assertEqual(g.effective_enforcement, HARD)
        self.assertEqual(default_enforcement(GUARDRAIL), HARD)

    def test_fact_enforcement_not_applicable_defaults_advisory(self):
        # facts carry no enforcement; effective_enforcement is defined but N/A
        self.assertEqual(MemoryItem.create("db", "postgres").effective_enforcement, ADVISORY)

    def test_explicit_enforcement_round_trips_through_json(self):
        r = MemoryItem.create("r", "cond", kind=RULE, enforcement=SOFT)
        d = r.to_dict()
        self.assertEqual(d["enforcement"], SOFT)
        back = MemoryItem.from_dict(d)
        self.assertEqual(back.enforcement, SOFT)
        self.assertEqual(back.effective_enforcement, SOFT)

    def test_legacy_doc_without_enforcement_reads_advisory(self):
        legacy = MemoryItem.from_dict({"subject": "x", "value": "y", "kind": "rule"})
        self.assertEqual(legacy.effective_enforcement, ADVISORY)

    def test_legacy_guardrail_doc_reads_hard(self):
        legacy = MemoryItem.from_dict({"subject": "x", "value": "y", "kind": "guardrail"})
        self.assertEqual(legacy.effective_enforcement, HARD)

    def test_enforcement_persists_across_a_store_write(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)
            store.remember(MemoryItem.create("r", "cond", kind=RULE, id="r1"), assume_yes=True)
            self.assertEqual(store.get("r1").effective_enforcement, ADVISORY)


# ============================================================ precedence_class is enforcement-driven
class TestPrecedenceClass(unittest.TestCase):
    def _item(self, kind, enforcement=""):
        return MemoryItem.create("k", "v", kind=kind, enforcement=enforcement)

    def test_hard_rule_is_safety(self):
        self.assertEqual(P.precedence_class(self._item(RULE, HARD)), P.SAFETY)
        self.assertEqual(P.precedence_class(self._item(GUARDRAIL)), P.SAFETY)   # guardrail→hard

    def test_advisory_and_soft_rules_are_preference(self):
        self.assertEqual(P.precedence_class(self._item(RULE)), P.PREFERENCE)         # advisory
        self.assertEqual(P.precedence_class(self._item(RULE, SOFT)), P.PREFERENCE)

    def test_facts_and_formulae_are_preference(self):
        self.assertEqual(P.precedence_class(self._item("")), P.PREFERENCE)           # fact
        self.assertEqual(P.precedence_class(self._item(FORMULA)), P.PREFERENCE)

    def test_promoting_to_hard_flips_the_class(self):
        it = self._item(RULE)
        self.assertEqual(P.precedence_class(it), P.PREFERENCE)
        it.enforcement = HARD
        self.assertEqual(P.precedence_class(it), P.SAFETY)


# ============================================================ promotion: gated + ledgered + binding-only
class TestPromotion(unittest.TestCase):
    def _seed_rule(self, store, id="r1", enforcement=""):
        store.remember(MemoryItem.create("no-plaintext-secrets", "never store secrets in code",
                                         kind=RULE, id=id, enforcement=enforcement),
                       assume_yes=True)
        return store.get(id)

    def test_promote_advisory_to_soft_to_hard_changes_only_the_binding(self):
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d)
            before = self._seed_rule(store)
            self.assertEqual(before.effective_enforcement, ADVISORY)

            r1 = store.promote("r1", SOFT, assume_yes=True)
            self.assertTrue(r1.changed)
            self.assertEqual((r1.old, r1.new, r1.direction), (ADVISORY, SOFT, "promote"))

            r2 = store.promote("r1", HARD, assume_yes=True)
            self.assertTrue(r2.changed)
            self.assertEqual((r2.old, r2.new, r2.direction), (SOFT, HARD, "promote"))

            after = store.get("r1")
            self.assertEqual(after.effective_enforcement, HARD)
            # the CONDITION (value) + subject + kind are byte-identical — binding only
            self.assertEqual(after.value, before.value)
            self.assertEqual(after.subject, before.subject)
            self.assertEqual(after.kind, before.kind)

    def test_promotion_is_ledgered_with_who_oldnew_and_approval(self):
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d)
            self._seed_rule(store)
            store.promote("r1", HARD, assume_yes=True, actor="alice")
            recs = [e for e in led.entries() if e["kind"] == "enforcement_change"]
            self.assertEqual(len(recs), 1)
            e = recs[0]
            self.assertEqual((e["old"], e["new"], e["direction"]), (ADVISORY, HARD, "promote"))
            self.assertEqual(e["actor"], "alice")
            self.assertEqual(e["item_id"], "r1")
            # the approval reference points at a real WriteGate "approved" row
            gate = [g for g in led.entries() if g["kind"] == "write_gate"]
            self.assertTrue(any(g["seq"] == e["approval_seq"] and g["decision"] == "approved"
                                for g in gate))

    def test_demotion_is_gated_too(self):
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d)
            self._seed_rule(store, enforcement=HARD)
            res = store.promote("r1", ADVISORY, assume_yes=True)
            self.assertTrue(res.changed)
            self.assertEqual(res.direction, "demote")
            self.assertEqual(store.get("r1").effective_enforcement, ADVISORY)
            self.assertEqual(len([e for e in led.entries()
                                  if e["kind"] == "enforcement_change"]), 1)

    def test_declined_gate_changes_nothing_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d)
            self._seed_rule(store)
            before_writes = store.stats.writes
            res = store.promote("r1", HARD, confirm=lambda _t: False)   # user declines
            self.assertFalse(res.changed)
            self.assertTrue(res.aborted)
            self.assertEqual(store.get("r1").effective_enforcement, ADVISORY)  # unchanged on disk
            self.assertEqual(store.stats.writes, before_writes)               # nothing written
            self.assertEqual([e for e in led.entries()
                              if e["kind"] == "enforcement_change"], [])       # not ledgered

    def test_promotion_routes_through_the_writegate(self):
        # the gate is exercised: a declined confirm blocks, an approved confirm commits
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d)
            self._seed_rule(store)
            store.promote("r1", HARD, confirm=lambda _t: True)
            self.assertEqual(store.get("r1").effective_enforcement, HARD)
            self.assertTrue(any(g["kind"] == "write_gate" and g["decision"] == "approved"
                                for g in led.entries()))

    def test_noop_when_already_at_target(self):
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d)
            self._seed_rule(store, enforcement=SOFT)
            res = store.promote("r1", SOFT, assume_yes=True)
            self.assertFalse(res.changed)
            self.assertEqual(res.direction, "unchanged")
            self.assertFalse(res.aborted)
            self.assertEqual([e for e in led.entries()
                              if e["kind"] == "enforcement_change"], [])   # nothing ledgered

    def test_promote_refuses_a_non_rule(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)
            store.remember(MemoryItem.create("db", "postgres", id="f1"), assume_yes=True)
            res = store.promote("f1", HARD, assume_yes=True)
            self.assertFalse(res.changed)
            self.assertTrue(res.aborted)
            self.assertIn("only", res.message.lower())

    def test_promote_unknown_level_and_missing_item_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)
            self.assertFalse(store.promote("nope", HARD, assume_yes=True).changed)  # missing
            self._seed_rule(store)
            bad = store.promote("r1", "banana", assume_yes=True)                    # bad level
            self.assertFalse(bad.changed)
            self.assertTrue(bad.aborted)


# ============================================================ CLI surface
class TestPromoteCli(unittest.TestCase):
    def _repo(self, d):
        from mokata.init import init_repo
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)

    def _run(self, argv):
        import io
        import sys
        from mokata import cli
        out, err = io.StringIO(), io.StringIO()
        so, se = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            rc = cli.main(argv)
        finally:
            sys.stdout, sys.stderr = so, se
        return rc, out.getvalue() + err.getvalue()

    def test_promote_usage_error_without_to(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            rc, out = self._run(["memory", "promote", "someid", "--path", d])
            self.assertEqual(rc, 2)
            self.assertIn("--to", out)

    def test_promote_rejects_bad_level(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            rc, out = self._run(["memory", "promote", "someid", "--to", "banana",
                                 "--path", d])
            self.assertEqual(rc, 2)
            self.assertIn("advisory", out)


if __name__ == "__main__":
    unittest.main()
