"""TM.S11 — PM review workflow + roles (doc 62 §6, doc 63 §4/§5).

A proposed memory change moves Draft → In-Review → Approved → Published, each transition
human-gated + ledgered with a rendered diff. Covers the grooming decision exactly:

  * the state machine (legal transitions only; skipping review is refused, fail-closed);
  * separation of duties (HARD): the proposer may NOT self-approve; a non-approver can't approve;
  * required-review per project/category (CODEOWNERS-style over the S10 grant map): the named
    approver IS able; publish is blocked without a recorded required-approver sign-off;
  * audit + rendered diff on every transition; rollback restores the prior via supersede lineage;
  * the CLI `memory review` surface (list + approve/reject);
  * LOCAL mode: single user, no review required — byte-identical.

No live DB — the local SQLite floor + an in-memory ledger + a hand-built AccessPolicy cover it.
"""

import argparse
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.govern import AuditLedger
from mokata.memory.access import APPROVER, EDITOR, AccessPolicy
from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import ACTIVE, PROPOSED, REJECTED as REJECTED_STATUS, SUPERSEDED, MemoryItem
from mokata.memory.scope import PROJECT
from mokata.memory.store import MemoryStore, ReviewResult
from mokata.memory import review as R


def _store(tmp, *, identity=None, access=None):
    led = AuditLedger(os.path.join(tmp, "ledger.jsonl"))
    store = MemoryStore(SQLiteBackend(os.path.join(tmp, "m.db")), ledger=led,
                        identity=identity, access=access)
    return store, led


def _policy(grants, enforce=True):
    return AccessPolicy.from_grants(grants, enforce=enforce)


# team policy: alice edits, pm approves, at the project scope
_TEAM = {"project": {EDITOR: ["alice"], APPROVER: ["pm"]}}


def _proj_item(subject="api-style", value="use fastapi", id="p1"):
    return MemoryItem.create(subject, value, id=id, scope_level=PROJECT, scope_id="web")


# ============================================================ pure state machine (review.py)
class TestStateMachine(unittest.TestCase):
    def test_legal_forward_path(self):
        self.assertTrue(R.can_transition(R.DRAFT, R.IN_REVIEW))
        self.assertTrue(R.can_transition(R.IN_REVIEW, R.APPROVED))
        self.assertTrue(R.can_transition(R.APPROVED, R.PUBLISHED))

    def test_cannot_skip_review_fail_closed(self):
        # a change can NEVER reach Published without passing through Approved
        self.assertFalse(R.can_transition(R.DRAFT, R.PUBLISHED))
        self.assertFalse(R.can_transition(R.IN_REVIEW, R.PUBLISHED))
        self.assertFalse(R.can_transition(R.DRAFT, R.APPROVED))

    def test_terminal_states_have_no_exit(self):
        self.assertFalse(R.can_transition(R.PUBLISHED, R.DRAFT))
        self.assertFalse(R.can_transition(R.REJECTED, R.IN_REVIEW))
        self.assertTrue(R.is_terminal(R.PUBLISHED))
        self.assertTrue(R.is_terminal(R.REJECTED))

    def test_reject_legal_from_any_nonterminal(self):
        for s in (R.DRAFT, R.IN_REVIEW, R.APPROVED):
            self.assertTrue(R.can_transition(s, R.REJECTED))

    def test_unknown_state_refused(self):
        self.assertFalse(R.can_transition("bogus", R.IN_REVIEW))
        self.assertFalse(R.can_transition(R.DRAFT, "bogus"))


class TestSeparationOfDuties(unittest.TestCase):
    def test_self_approval_refused(self):
        self.assertFalse(R.separation_ok("alice", "alice"))

    def test_distinct_approver_ok(self):
        self.assertTrue(R.separation_ok("alice", "pm"))

    def test_missing_identities_refused_fail_closed(self):
        self.assertFalse(R.separation_ok("alice", ""))
        self.assertFalse(R.separation_ok("", "pm"))
        self.assertFalse(R.separation_ok(None, None))


class TestRequiredApprovers(unittest.TestCase):
    def test_named_approver_is_resolved(self):
        p = _policy(_TEAM)
        self.assertEqual(R.required_approvers(p, PROJECT), {"pm"})

    def test_category_narrows(self):
        p = _policy({"project:backend": {APPROVER: ["carol"]}})
        self.assertEqual(R.required_approvers(p, PROJECT, "backend"), {"carol"})
        self.assertEqual(R.required_approvers(p, PROJECT, "frontend"), set())

    def test_no_policy_empty(self):
        self.assertEqual(R.required_approvers(None, PROJECT), set())


class TestDiff(unittest.TestCase):
    def test_change_diff(self):
        base = MemoryItem.create("s", "old")
        prop = MemoryItem.create("s", "new")
        self.assertEqual(R.diff_line(base, prop), "'old' -> 'new'")

    def test_new_item_diff(self):
        self.assertEqual(R.diff_line(None, MemoryItem.create("s", "v")), "(new) -> 'v'")


# ============================================================ item round-trip
class TestItemRoundTrip(unittest.TestCase):
    def test_review_metadata_round_trips(self):
        it = MemoryItem.create("s", "v", review={"state": R.DRAFT, "proposer": "alice"})
        back = MemoryItem.from_dict(it.to_dict())
        self.assertEqual(back.review, {"state": R.DRAFT, "proposer": "alice"})

    def test_legacy_item_has_empty_review(self):
        back = MemoryItem.from_dict({"subject": "s", "value": "v"})   # no review key
        self.assertEqual(back.review, {})


# ============================================================ store — the full happy path
class TestHappyPath(unittest.TestCase):
    def test_draft_to_published_each_transition_ledgered_with_diff(self):
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d, identity="alice", access=_policy(_TEAM))
            # a published baseline the edit will supersede
            store.backend.put(_proj_item(value="use flask", id="base"))
            new = _proj_item(value="use fastapi", id="prop")

            r1 = store.propose(new, base_id="base", change="edit", assume_yes=True)
            self.assertTrue(r1.ok and r1.state == R.DRAFT)
            self.assertEqual(store.get("prop").status, PROPOSED)      # NOT live yet
            self.assertNotIn("prop", {i.id for i in store.all_active()})

            r2 = store.submit_for_review("prop", assume_yes=True)
            self.assertTrue(r2.ok and r2.state == R.IN_REVIEW)

            r3 = store.approve("prop", actor="pm", assume_yes=True)   # distinct approver
            self.assertTrue(r3.ok and r3.state == R.APPROVED)
            self.assertEqual(store.get("prop").review["approver"], "pm")

            r4 = store.publish("prop", actor="pm", assume_yes=True)
            self.assertTrue(r4.ok and r4.state == R.PUBLISHED)

            # now LIVE, and it superseded the baseline (rollback lineage)
            self.assertEqual(store.get("prop").status, ACTIVE)
            self.assertIn("prop", {i.id for i in store.all_active()})
            self.assertEqual(store.get("base").status, SUPERSEDED)
            self.assertIn("base", store.get("prop").supersedes)

            # every transition ledgered with a diff + from→to
            trans = [e for e in led.entries() if e["kind"] == "review_transition"]
            self.assertEqual([e["to"] for e in trans],
                             [R.DRAFT, R.IN_REVIEW, R.APPROVED, R.PUBLISHED])
            for e in trans:
                self.assertIn("diff", e)
                # ledgered approval references a real approved write-gate row (P2)
                gate = [g for g in led.entries()
                        if g["kind"] == "write_gate" and g["seq"] == e["approval_seq"]]
                self.assertTrue(any(g["decision"] == "approved" for g in gate))
            # the publish transition's diff is the "what changed"
            pub = [e for e in trans if e["to"] == R.PUBLISHED][0]
            self.assertEqual(pub["diff"], "'use flask' -> 'use fastapi'")


# ============================================================ store — separation of duties (HARD)
class TestSoDEnforcement(unittest.TestCase):
    def _to_in_review(self, store):
        store.propose(_proj_item(id="prop"), change="new", assume_yes=True)
        store.submit_for_review("prop", assume_yes=True)

    def test_proposer_cannot_self_approve(self):
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d, identity="alice", access=_policy(_TEAM))
            self._to_in_review(store)
            res = store.approve("prop", actor="alice", assume_yes=True)   # self-approval
            self.assertFalse(res.ok)
            self.assertTrue(res.aborted)
            self.assertIn("self-approve", res.message)
            self.assertEqual(store.get("prop").review["state"], R.IN_REVIEW)   # unchanged
            # refused BEFORE the gate — no approved transition ledgered for APPROVED
            self.assertEqual([e for e in led.entries()
                              if e["kind"] == "review_transition" and e["to"] == R.APPROVED], [])

    def test_non_approver_cannot_approve(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice", access=_policy(_TEAM))
            self._to_in_review(store)
            res = store.approve("prop", actor="bob", assume_yes=True)   # bob has no approver role
            self.assertFalse(res.ok)
            self.assertIn("not an approver", res.message)
            self.assertEqual(store.get("prop").review["state"], R.IN_REVIEW)

    def test_named_required_approver_is_able(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice", access=_policy(_TEAM))
            self._to_in_review(store)
            res = store.approve("prop", actor="pm", assume_yes=True)     # the named PM
            self.assertTrue(res.ok)
            self.assertEqual(store.get("prop").review["state"], R.APPROVED)


# ============================================================ store — publish gate / required review
class TestPublishGate(unittest.TestCase):
    def test_publish_blocked_without_review(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice", access=_policy(_TEAM))
            store.propose(_proj_item(id="prop"), change="new", assume_yes=True)
            store.submit_for_review("prop", assume_yes=True)
            # skip approval → publish directly: refused, fail-closed (can't skip review)
            res = store.publish("prop", actor="pm", assume_yes=True)
            self.assertFalse(res.ok)
            self.assertIn("illegal transition", res.message)
            self.assertEqual(store.get("prop").status, PROPOSED)        # never went live

    def test_reject_is_terminal(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice", access=_policy(_TEAM))
            store.propose(_proj_item(id="prop"), change="new", assume_yes=True)
            res = store.reject("prop", actor="pm", assume_yes=True)
            self.assertTrue(res.ok and res.state == R.REJECTED)
            self.assertEqual(store.get("prop").status, REJECTED_STATUS)
            self.assertNotIn("prop", {i.id for i in store.pending_reviews()})


# ============================================================ store — rollback via supersede lineage
class TestRollback(unittest.TestCase):
    def test_rollback_restores_prior(self):
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d, identity="alice", access=_policy(_TEAM))
            store.backend.put(_proj_item(value="use flask", id="base"))
            store.propose(_proj_item(value="use fastapi", id="prop"), base_id="base",
                          change="edit", assume_yes=True)
            store.submit_for_review("prop", assume_yes=True)
            store.approve("prop", actor="pm", assume_yes=True)
            store.publish("prop", actor="pm", assume_yes=True)
            self.assertEqual(store.get("base").status, SUPERSEDED)

            res = store.rollback("prop", actor="pm", assume_yes=True)
            self.assertTrue(res.ok)
            self.assertEqual(store.get("base").status, ACTIVE)         # prior restored
            self.assertEqual(store.get("prop").status, SUPERSEDED)     # the change stepped aside
            self.assertIn("base", {i.id for i in store.all_active()})
            self.assertTrue([e for e in led.entries() if e["kind"] == "review_rollback"])

    def test_rollback_needs_approver_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice", access=_policy(_TEAM))
            store.backend.put(_proj_item(value="old", id="base"))
            item = _proj_item(value="new", id="prop")
            item.supersedes = ["base"]
            item.status = ACTIVE
            store.backend.put(item)
            res = store.rollback("prop", actor="bob", assume_yes=True)  # bob not an approver
            self.assertFalse(res.ok)
            self.assertIn("not an approver", res.message)

    def test_rollback_no_lineage_refused(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice", access=_policy(_TEAM))
            store.backend.put(_proj_item(id="lonely"))
            res = store.rollback("lonely", actor="pm", assume_yes=True)
            self.assertFalse(res.ok)
            self.assertIn("nothing to roll back", res.message)


# ============================================================ store — pending list + fail-closed
class TestPending(unittest.TestCase):
    def test_pending_lists_draft_inreview_approved_only(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice", access=_policy(_TEAM))
            store.propose(_proj_item(id="draft1"), change="new", assume_yes=True)
            store.propose(_proj_item(id="pub1"), change="new", assume_yes=True)
            store.submit_for_review("pub1", assume_yes=True)
            store.approve("pub1", actor="pm", assume_yes=True)
            store.publish("pub1", actor="pm", assume_yes=True)          # now live, not pending
            ids = {i.id for i in store.pending_reviews()}
            self.assertEqual(ids, {"draft1"})

    def test_transition_on_nonproposal_refused(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice", access=_policy(_TEAM))
            store.backend.put(_proj_item(id="plain"))                   # not a proposal
            res = store.approve("plain", actor="pm", assume_yes=True)
            self.assertFalse(res.ok)
            self.assertIn("not a proposal", res.message)


# ============================================================ local mode — no review (byte-identical)
class TestLocalMode(unittest.TestCase):
    def test_review_not_required_locally(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)                                       # no access policy
            self.assertFalse(store.review_required())

    def test_review_required_only_when_enforcing(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice", access=_policy(_TEAM))
            self.assertTrue(store.review_required())
            store2, _ = _store(d, identity="a", access=_policy(_TEAM, enforce=False))
            self.assertFalse(store2.review_required())

    def test_local_remember_publishes_directly_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)                                       # local: no policy
            store.remember(MemoryItem.create("db", "postgres", id="f1"), assume_yes=True)
            self.assertEqual(store.get("f1").status, ACTIVE)           # live immediately
            self.assertIn("f1", {i.id for i in store.all_active()})


# ============================================================ CLI surface — `memory review`
class TestCliReview(unittest.TestCase):
    def _args(self, **kw):
        base = dict(yes=True, submit=None, approve=None, reject=None,
                    publish=None, rollback=None)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_list_shows_pending_with_diff(self):
        from mokata.cli_commands.memory import _memory_review
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice", access=_policy(_TEAM))
            store.backend.put(_proj_item(value="use flask", id="base"))
            store.propose(_proj_item(value="use fastapi", id="prop"), base_id="base",
                          change="edit", assume_yes=True)
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = _memory_review(store, self._args())
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("prop", out)
            self.assertIn("use flask", out)                            # rendered diff shown
            self.assertIn("pm", out)                                   # required approver named

    def test_cli_approve_then_publish(self):
        from mokata.cli_commands.memory import _memory_review
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="pm", access=_policy(_TEAM))
            # alice proposes; pm (the CLI identity) approves + publishes
            store.propose(_proj_item(id="prop"), change="new", proposer="alice",
                          assume_yes=True)
            store.submit_for_review("prop", assume_yes=True)
            self.assertEqual(_memory_review(store, self._args(approve="prop")), 0)
            self.assertEqual(store.get("prop").review["state"], R.APPROVED)
            self.assertEqual(_memory_review(store, self._args(publish="prop")), 0)
            self.assertEqual(store.get("prop").status, ACTIVE)

    def test_cli_reject(self):
        from mokata.cli_commands.memory import _memory_review
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="pm", access=_policy(_TEAM))
            store.propose(_proj_item(id="prop"), change="new", proposer="alice",
                          assume_yes=True)
            self.assertEqual(_memory_review(store, self._args(reject="prop")), 0)
            self.assertEqual(store.get("prop").status, REJECTED_STATUS)


if __name__ == "__main__":
    unittest.main()
