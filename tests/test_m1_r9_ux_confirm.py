"""M-1/R9 (S3) — UX-CONFIRM minimum slice (D7 a/b/c): dedup the ASK, never the GATE.

The live pain (Jas 2026-07-17, doc 84 §6): the same thing gets confirmed several times across one
pipeline run. The principle that bounds the fix is doc 84's own, and it is narrow on purpose:
**every human DECISION is asked exactly once, with full content; no gate is removed.** P2 and P14
are untouched. This de-duplicates asks, not gates.

D7's minimum slice, all three parts:
  (a) `write_gate` ledger entries gain `content_hash` + `run`. They carried NEITHER, so nothing
      downstream could even ask "has this artifact already been approved in this run" — the
      re-ask problem was not merely unfixed, it was unmeasurable.
  (b) carry-forward on the approval path, mirroring `approval.propose`'s live-proposal
      short-circuit: a human who already said yes to this exact content, in this run, is not
      asked again.
  (c) grouped same-kind proposals: one ask, every member rendered in full, per-item ledger
      entries.

TTY-chokepoint dedup and the FIND-TRIAGE declined-memory slice are deliberately OUT (D7).

The contracts, each with the mutation it catches:

   9. `content_hash` + `run` on every gate decision. RED when either is dropped — carry-forward
      then has nothing to join on and silently never fires (or, worse, fires on a partial match).
  10. Carry-forward requires the same hash AND the same run. Content-only would let an approval be
      banked and redeemed against a later, differently-framed conversation — the SI.3 threat model
      (`approval.DEFAULT_TTL_SECONDS`'s own reasoning). Run-only would let any approval in the run
      license any content. RED when either half of the predicate is relaxed.
  11. **NON-NEGOTIABLE.** Dedup removes an ask, never a gate:
        * a first, un-approved write still prompts;
        * the number of gate DECISIONS recorded never decreases — a carried write still gets its
          own `approved` entry, naming what it was carried from;
        * nothing short-circuits ahead of self-protect, the trust dial, the secret scan or
          governance enforcement. A secret introduced into content that hashes the same as
          something approved earlier still BLOCKS. A target that became unwritable still BLOCKS.
      RED when the carry-forward check is hoisted above any security layer.
  12. A grouped proposal renders ALL members in full (never truncated — that would turn "one
      decision with full content" into "one decision with some of it", reintroducing the very
      rubber-stamp R9 closes) and produces a per-item ledger entry.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata import approval
from mokata.govern.gate import WriteGate, WriteRequest, content_hash_for
from mokata.govern.ledger import AuditLedger

RUN = "run-a"
OTHER_RUN = "run-b"

# A real AWS-shaped key, ASSEMBLED AT RUNTIME rather than written as a literal — mokata's own
# secret-guard (correctly) blocks this file otherwise, and that block is NOT a false positive: the
# shape IS the documented credential format. Composing it keeps the test's teeth (the scanner sees
# the joined string at scan time — verified) while the source file carries no credential shape.
# Deliberately not ignore-listed: a version-controlled ignore entry for a real key shape is a worse
# artifact than a `+`, and the SECRET-FP row is where genuine false positives belong.
SECRET = "AKIA" + "IOSFODNN7EXAMPLE"


class _Gate:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = AuditLedger(os.path.join(self.tmp.name, "audit", "ledger.jsonl"))
        self.gate = WriteGate(ledger=self.ledger)
        self.asked = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()

    def confirm(self, _text):
        self.asked += 1
        return True

    def refuse(self, _text):
        self.asked += 1
        return False

    def submit(self, content="v", *, run=RUN, target="memory:s", kind="memory",
               confirm=None, **kw):
        return self.gate.submit(
            WriteRequest(kind, target, content=content, actor="memory", run=run),
            commit=lambda: None, confirm=confirm or self.confirm, **kw)

    def decisions(self):
        return [e for e in self.ledger.entries() if e.get("kind") == "write_gate"]

    def approvals(self):
        return [e for e in self.decisions() if e.get("decision") == "approved"]


# ================================================ pin 9 — the entries carry what a join needs
class GateEntriesCarryContentHashAndRun(unittest.TestCase):

    def test_an_approved_entry_records_both(self):
        with _Gate() as g:
            g.submit("hello")
            [entry] = g.approvals()
            self.assertEqual(RUN, entry["run"])
            self.assertEqual(64, len(entry["content_hash"]))

    def test_a_declined_entry_records_both_too(self):
        """A decline is a decision about an artifact, and an audit of confirmation economics needs
        to see the asks that were answered NO as much as the ones answered yes."""
        with _Gate() as g:
            g.submit("hello", confirm=g.refuse)
            [entry] = g.decisions()
            self.assertEqual("declined", entry["decision"])
            self.assertEqual(RUN, entry["run"])
            self.assertTrue(entry["content_hash"])

    def test_the_hash_covers_content_target_and_kind_only(self):
        """Identity fields are OUT of the pre-image: they say who is asking, not what is written.
        Folding them in would mean the same artifact re-proposed through another surface no longer
        matched the approval just given — re-asking for the exact reason D7 exists to remove."""
        base = WriteRequest("memory", "memory:s", content="v", actor="memory", run=RUN)
        same = WriteRequest("memory", "memory:s", content="v", actor="cli", tool="t",
                            surface="mcp", run=OTHER_RUN)
        self.assertEqual(content_hash_for(base), content_hash_for(same))

        for differing in (WriteRequest("memory", "memory:s", content="CHANGED"),
                          WriteRequest("memory", "memory:OTHER", content="v"),
                          WriteRequest("config", "memory:s", content="v")):
            self.assertNotEqual(content_hash_for(base), content_hash_for(differing))

    def test_a_caller_that_threads_no_run_records_none(self):
        with _Gate() as g:
            g.submit("hello", run=None)
            self.assertIsNone(g.approvals()[-1]["run"])


# ==================================================== pin 10 — same hash AND same run, or ask
class CarryForwardIsBoundToContentAndRun(unittest.TestCase):

    def test_the_same_artifact_in_the_same_run_is_not_re_asked(self):
        with _Gate() as g:
            g.submit("hello")
            g.submit("hello")
            g.submit("hello")
            self.assertEqual(1, g.asked)              # asked once
            self.assertEqual(3, len(g.approvals()))   # recorded three times

    def test_different_content_in_the_same_run_asks_again(self):
        with _Gate() as g:
            g.submit("hello")
            g.submit("something else entirely")
            self.assertEqual(2, g.asked)

    def test_the_same_content_in_a_different_run_asks_again(self):
        """The SI.3 threat model, applied here: an approval that carried across runs could be
        banked and redeemed against a much later, differently-framed conversation. A run is the
        scope in which the human still holds the context they approved with."""
        with _Gate() as g:
            g.submit("hello", run=RUN)
            g.submit("hello", run=OTHER_RUN)
            self.assertEqual(2, g.asked)

    def test_a_write_with_no_run_never_carries(self):
        """Fail-closed on absence. An unbounded approval is not carried — and this is also what
        makes every pre-0.0.16 caller byte-identical, including in how often it asks."""
        with _Gate() as g:
            g.submit("hello", run=None)
            g.submit("hello", run=None)
            self.assertEqual(2, g.asked)

    def test_a_declined_ask_is_never_carried_forward(self):
        """Only an APPROVED entry licenses anything. RED when the join stops filtering on
        `decision`: a refusal would start silently authorising the write it refused."""
        with _Gate() as g:
            g.submit("hello", confirm=g.refuse)
            out = g.submit("hello", confirm=g.refuse)
            self.assertEqual(2, g.asked)
            self.assertFalse(out.committed)

    def test_an_unreadable_ledger_asks_rather_than_assumes(self):
        with _Gate() as g:
            g.submit("hello")

            def _boom():
                raise OSError("ledger gone")
            g.ledger.entries = _boom
            g.submit("hello")
            self.assertEqual(2, g.asked)


# ================================================= pin 11 — an ASK is removed, a GATE never is
class DedupRemovesAnAskNeverAGate(unittest.TestCase):
    """The non-negotiable class. Every one of these is a way the optimisation could have become a
    security regression, asked as a behaviour rather than as a code-shape assertion."""

    def test_a_first_un_approved_write_still_prompts(self):
        with _Gate() as g:
            g.submit("brand new")
            self.assertEqual(1, g.asked)

    def test_the_number_of_recorded_decisions_never_decreases(self):
        """One ledger decision per durable write, carried or not. RED when a carried write returns
        early without recording — the audit trail would lose a write entirely."""
        with _Gate() as g:
            for _ in range(5):
                g.submit("hello")
            self.assertEqual(5, len(g.approvals()))
            self.assertEqual(1, g.asked)

    def test_a_carried_approval_names_what_it_was_carried_from(self):
        with _Gate() as g:
            g.submit("hello")
            first = g.approvals()[0]["seq"]
            g.submit("hello")
            self.assertIn(f"carried forward from #{first}", g.approvals()[-1]["reason"])

    def test_a_secret_still_blocks_content_that_was_approved_before(self):
        """THE pin. An approval licenses CONTENT, and never anything else. Approve a clean write,
        then submit content carrying a secret in the same run — the scan runs on every submit
        regardless, because it sits AHEAD of the carry-forward check."""
        with _Gate() as g:
            g.submit("clean content")
            out = g.submit(f"clean content {SECRET}")
            self.assertFalse(out.committed)
            self.assertTrue(out.findings)
            self.assertEqual("blocked", g.decisions()[-1]["decision"])

    def test_a_secret_blocks_even_on_an_exactly_carried_hash(self):
        """The adversarial shape: an approved entry is planted whose hash matches EXACTLY the
        secret-bearing body about to be submitted. Carry-forward would fire on it — and the write
        is blocked anyway, because layer 1 runs first. RED the moment the check is hoisted."""
        with _Gate() as g:
            secret_body = f"payload {SECRET}"
            g.ledger.record("write_gate", write_kind="memory", target="memory:s", actor="memory",
                            decision="approved", reason="committed",
                            content_hash=content_hash_for(
                                WriteRequest("memory", "memory:s", content=secret_body)),
                            run=RUN)
            out = g.submit(secret_body)
            self.assertFalse(out.committed)
            self.assertTrue(out.findings)
            self.assertEqual(0, g.asked)      # not asked — but blocked, which is the point
            self.assertEqual("blocked", g.decisions()[-1]["decision"])

    def test_self_protect_still_blocks_a_carried_hash(self):
        """Layer 0 runs first, always. An approval cannot whitelist a tree that is never writable."""
        import mokata.selfprotect as sp
        with _Gate() as g:
            target = os.path.join(os.path.dirname(sp.__file__), "gate.py")
            forged = WriteRequest("code", target, content="x", actor="agent", run=RUN)
            g.ledger.record("write_gate", write_kind="code", target=target, actor="agent",
                            decision="approved", reason="committed",
                            content_hash=content_hash_for(forged), run=RUN)
            out = g.gate.submit(forged, commit=lambda: None, confirm=g.confirm)
            self.assertFalse(out.committed)
            self.assertEqual(0, g.asked)
            self.assertEqual("blocked", g.decisions()[-1]["decision"])

    def test_a_blocked_write_is_never_recorded_as_approved(self):
        with _Gate() as g:
            g.submit(f"has {SECRET}")
            self.assertEqual([], g.approvals())

    def test_a_governance_block_survives_a_carried_hash(self):
        """Layer 2 (TM.S8/P14) also runs ahead of the carry. A rule that fires now blocks a write
        whose content was approved before the rule existed."""
        from mokata.govern.enforce import EnforcementGate

        class _Blocked:
            allowed = False
            message = "blocked by rule"
            verdict = None
            overridden = False

        with _Gate() as g:
            g.submit("hello")                     # approved, so the hash is carriable
            original = EnforcementGate.check
            EnforcementGate.check = lambda self, *a, **kw: _Blocked()
            try:
                out = g.gate.submit(
                    WriteRequest("memory", "memory:s", content="hello", actor="memory", run=RUN),
                    commit=lambda: None, confirm=g.confirm,
                    rules=[object()], action=object())
            finally:
                EnforcementGate.check = original
            self.assertFalse(out.committed)
            self.assertEqual("blocked", g.decisions()[-1]["decision"])

    def test_assume_yes_and_human_approved_are_untouched(self):
        """The two existing clearances behave exactly as before — carry-forward is a third path,
        not a replacement for either."""
        with _Gate() as g:
            self.assertTrue(g.submit("a", assume_yes=True).committed)
            self.assertTrue(g.submit("b", human_approved=True).committed)
            self.assertEqual(0, g.asked)


# ============================================= pin 12 — grouped proposals: one ask, full content
class GroupedProposalsShowEverythingTheyApprove(unittest.TestCase):

    def _members(self, n=3):
        return [{"tool": "memory_write", "target": f"memory:item{i}",
                 "args": {"subject": f"item{i}", "value": f"value{i}"},
                 "preview": f"subject: item{i}\nvalue: value{i}"} for i in range(n)]

    def test_one_proposal_covers_the_whole_set(self):
        with tempfile.TemporaryDirectory() as root:
            members = self._members()
            p = approval.propose_group(root, tool="memory_write", members=members, run_id=RUN)
            self.assertEqual(approval.group_content_hash(members), p.content_hash)
            self.assertEqual(p, approval.load(root, p.proposal_id))

    def test_every_member_renders_in_full_and_is_never_truncated(self):
        """The whole justification for asking once instead of N times is that the human sees all N.
        A truncated group render would be "one decision with SOME of the content" — the rubber-stamp
        R9 closes, reintroduced through the confirmation-economics door. Sized past `render`'s
        20-line cap on purpose."""
        with tempfile.TemporaryDirectory() as root:
            members = self._members(20)
            p = approval.propose_group(root, tool="memory_write", members=members, run_id=RUN)
            shown = approval.render_group(p)
            for i in range(20):
                self.assertIn(f"item{i}", shown)
                self.assertIn(f"value{i}", shown)
            self.assertIn("one approval covers exactly this set", shown)

    def test_changing_the_set_invalidates_the_approval(self):
        """SI.3 content-binding, extended to the group hash: adding, removing or editing a member
        makes it a different set, which the human has not seen."""
        members = self._members(3)
        base = approval.group_content_hash(members)
        self.assertNotEqual(base, approval.group_content_hash(members[:2]))
        self.assertNotEqual(base, approval.group_content_hash(members + self._members(1)))

        edited = [dict(m) for m in members]
        edited[1] = dict(edited[1], args={"subject": "item1", "value": "TAMPERED"})
        self.assertNotEqual(base, approval.group_content_hash(edited))

    def test_reordering_the_set_invalidates_the_approval(self):
        """Order is part of the identity — "the same items in a different order" is a different
        thing to have read, and sorting here would let a re-ordered render redeem an approval for
        a list the human saw differently."""
        members = self._members(3)
        self.assertNotEqual(approval.group_content_hash(members),
                            approval.group_content_hash(list(reversed(members))))

    def test_redemption_records_one_entry_per_member(self):
        """One approval, N records. An entry saying "5 writes were approved" cannot answer the
        audit question, which is asked of one row at a time: what licensed THIS write."""
        class _Ledger:
            def __init__(self):
                self.rows = []

            def record(self, kind, **fields):
                self.rows.append(dict(fields, kind=kind))
                return {"seq": len(self.rows)}

        with tempfile.TemporaryDirectory() as root:
            members = self._members(4)
            p = approval.propose_group(root, tool="memory_write", members=members, run_id=RUN)
            ledger = _Ledger()
            approval.record_group_redemption(ledger, p, members, write_seqs=[10, 11, 12, 13])

            self.assertEqual(4, len(ledger.rows))
            self.assertEqual([1, 2, 3, 4], [r["group_index"] for r in ledger.rows])
            self.assertEqual([10, 11, 12, 13], [r["write_seq"] for r in ledger.rows])
            # each names its OWN write and the ONE decision it rode
            self.assertEqual({p.content_hash}, {r["group_hash"] for r in ledger.rows})
            self.assertEqual(4, len({r["content_hash"] for r in ledger.rows}))

    def test_re_proposing_the_same_group_is_idempotent(self):
        """`propose`'s rule, inherited: re-proposing must never reset an approval already given
        (nor extend its clock)."""
        with tempfile.TemporaryDirectory() as root:
            members = self._members()
            first = approval.propose_group(root, tool="memory_write", members=members, run_id=RUN)
            again = approval.propose_group(root, tool="memory_write", members=members, run_id=RUN)
            self.assertEqual(first.proposal_id, again.proposal_id)
            self.assertEqual(first.expires_at, again.expires_at)


if __name__ == "__main__":
    unittest.main()
