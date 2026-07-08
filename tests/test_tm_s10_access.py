"""TM.S10 — identity + scoped-consent access control (doc 52 M-1/M-2, doc 62 §6, doc 63 §4).

Covers the grooming decision exactly (first Phase-C stage):
  * IDENTITY = the run identity (team_audit.actor() / session_bundle._default_author()) — the REAL
    author is stamped on every durable write; no more "user" everywhere. Legacy items unaffected;
  * ACCESS GRANTS are CONFIG-BASED (CODEOWNERS-style, per scope+category →
    {viewer, editor, approver, promotion-approver}) — NO new DDL. RBAC ladder (higher ⊇ lower);
  * ENFORCEMENT is FAIL-CLOSED (deny on doubt): can_read/can_edit gate reads/edits; a non-permitted
    write is refused (nothing written); an unreadable item is filtered out; undeterminable → deny;
  * SCOPE PROMOTION to a BROADER scope requires the promotion-approver role — gated + ledgered,
    fail-closed. SEPARATE from S7's enforcement-level promotion;
  * LOCAL/personal mode = single user, full personal access — byte-identical to today (no policy).

No live DB — the local SQLite floor + an in-memory ledger + a hand-built AccessPolicy cover it.
"""

import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

from mokata.govern import AuditLedger
from mokata.memory.access import (
    APPROVER,
    EDITOR,
    PROMOTION_APPROVER,
    ROLES,
    VIEWER,
    AccessPolicy,
)
from mokata.memory.backends import SQLiteBackend
from mokata.memory.item import MemoryItem
from mokata.memory.scope import CATEGORY, GLOBAL, PERSONAL, PROJECT, TEAM
from mokata.memory.store import MemoryStore, ScopePromoteResult


def _store(tmp, *, identity=None, access=None):
    led = AuditLedger(os.path.join(tmp, "ledger.jsonl"))
    store = MemoryStore(SQLiteBackend(os.path.join(tmp, "m.db")), ledger=led,
                        identity=identity, access=access)
    return store, led


def _policy(grants, enforce=True):
    return AccessPolicy.from_grants(grants, enforce=enforce)


# ============================================================ AccessPolicy — roles + ladder
class TestAccessPolicyRoles(unittest.TestCase):
    def test_roles_are_the_four_team_roles(self):
        self.assertEqual(set(ROLES), {VIEWER, EDITOR, APPROVER, PROMOTION_APPROVER})

    def test_ladder_higher_role_implies_lower_ones(self):
        p = _policy({"project": {APPROVER: ["alice"]}})
        # an approver can read + edit (ladder: approver ⊇ editor ⊇ viewer)
        self.assertTrue(p.can_read("alice", PROJECT))
        self.assertTrue(p.can_edit("alice", PROJECT))
        # … but is NOT a promotion-approver
        self.assertFalse(p.can_promote_scope("alice", PROJECT))

    def test_viewer_reads_but_cannot_edit(self):
        p = _policy({"project": {VIEWER: ["bob"]}})
        self.assertTrue(p.can_read("bob", PROJECT))
        self.assertFalse(p.can_edit("bob", PROJECT))

    def test_promotion_approver_is_the_top_of_the_ladder(self):
        p = _policy({"team": {PROMOTION_APPROVER: ["lead"]}})
        self.assertTrue(p.can_promote_scope("lead", TEAM))
        self.assertTrue(p.can_edit("lead", TEAM))
        self.assertTrue(p.can_read("lead", TEAM))

    def test_category_grant_is_scoped_to_that_category(self):
        p = _policy({"project:backend": {EDITOR: ["carol"]}})
        self.assertTrue(p.can_edit("carol", PROJECT, "backend"))
        self.assertFalse(p.can_edit("carol", PROJECT, "frontend"))   # different category
        # a scope-level (no category) grant applies to every category in that scope
        p2 = _policy({"project": {EDITOR: ["carol"]}})
        self.assertTrue(p2.can_edit("carol", PROJECT, "backend"))


# ============================================================ AccessPolicy — fail-closed
class TestAccessPolicyFailClosed(unittest.TestCase):
    def test_no_grant_denies(self):
        p = _policy({"project": {EDITOR: ["alice"]}})
        self.assertFalse(p.can_read("stranger", PROJECT))
        self.assertFalse(p.can_edit("stranger", PROJECT))

    def test_missing_identity_denies(self):
        p = _policy({"project": {VIEWER: ["alice"]}})
        self.assertFalse(p.can_read("", PROJECT))
        self.assertFalse(p.can_read(None, PROJECT))

    def test_undeterminable_grant_denies(self):
        # a malformed grant block (not a role→ids map) must never grant — deny on doubt.
        p = _policy({"project": "not-a-dict"})
        self.assertFalse(p.can_read("alice", PROJECT))
        self.assertFalse(p.can_edit("alice", PROJECT))

    def test_unknown_role_is_ignored_not_granted(self):
        p = _policy({"project": {"superuser": ["alice"]}})
        self.assertFalse(p.can_read("alice", PROJECT))


# ============================================================ AccessPolicy — personal + local
class TestAccessPolicyPersonalAndLocal(unittest.TestCase):
    def test_owner_has_full_access_to_their_own_personal_scope(self):
        p = _policy({})   # no grants at all
        self.assertTrue(p.can_read("alice", PERSONAL, owner="alice"))
        self.assertTrue(p.can_edit("alice", PERSONAL, owner="alice"))
        # a legacy / empty-owner personal item is everyone's own (byte-identical local default)
        self.assertTrue(p.can_read("alice", PERSONAL, owner=""))

    def test_another_users_personal_item_is_denied(self):
        p = _policy({})
        self.assertFalse(p.can_read("bob", PERSONAL, owner="alice"))
        self.assertFalse(p.can_edit("bob", PERSONAL, owner="alice"))

    def test_personal_owner_cannot_self_promote_scope(self):
        # owning your personal scope is NOT authority to broaden it — needs promotion-approver.
        p = _policy({})
        self.assertFalse(p.can_promote_scope("alice", TEAM))

    def test_non_enforcing_policy_allows_everything_local_mode(self):
        p = _policy({"project": {EDITOR: ["alice"]}}, enforce=False)
        self.assertTrue(p.can_read("anyone", PROJECT))
        self.assertTrue(p.can_edit("anyone", PROJECT))
        self.assertTrue(p.can_promote_scope("anyone", GLOBAL))


# ============================================================ store — real author stamping (M-1)
class TestRealAuthor(unittest.TestCase):
    def test_durable_write_carries_the_real_author_not_user(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice")
            store.remember(MemoryItem.create("db", "postgres", id="f1"), assume_yes=True)
            self.assertEqual(store.get("f1").provenance["author"], "alice")

    def test_an_explicit_author_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d, identity="alice")
            store.remember(MemoryItem.create("db", "pg", id="f1", author="carol"),
                           assume_yes=True)
            self.assertEqual(store.get("f1").provenance["author"], "carol")

    def test_no_identity_leaves_the_placeholder_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)               # no identity → local/direct construction
            store.remember(MemoryItem.create("db", "pg", id="f1"), assume_yes=True)
            self.assertEqual(store.get("f1").provenance["author"], "user")


# ============================================================ store — can_edit gates writes
class TestEditEnforcement(unittest.TestCase):
    def _proj_item(self, id):
        return MemoryItem.create("api-style", "use fastapi", id=id,
                                 scope_level=PROJECT, scope_id="web")

    def test_non_permitted_identity_write_is_refused_nothing_written(self):
        with tempfile.TemporaryDirectory() as d:
            policy = _policy({"project": {EDITOR: ["alice"]}})
            store, led = _store(d, identity="bob", access=policy)   # bob has no grant
            before = store.stats.writes
            res = store.remember(self._proj_item("p1"), assume_yes=True)
            self.assertFalse(res.committed)
            self.assertTrue(res.aborted)
            self.assertIsNone(store.get("p1"))                       # nothing written
            self.assertEqual(store.stats.writes, before)
            # refused BEFORE the gate — no write_gate row for it
            self.assertEqual([e for e in led.entries() if e["kind"] == "write_gate"], [])

    def test_permitted_identity_write_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            policy = _policy({"project": {EDITOR: ["alice"]}})
            store, _ = _store(d, identity="alice", access=policy)
            res = store.remember(self._proj_item("p1"), assume_yes=True)
            self.assertTrue(res.committed)
            self.assertEqual(store.get("p1").provenance["author"], "alice")

    def test_owner_can_always_write_their_personal_scope_in_team_mode(self):
        with tempfile.TemporaryDirectory() as d:
            policy = _policy({"project": {EDITOR: ["alice"]}})   # no personal grant for bob
            store, _ = _store(d, identity="bob", access=policy)
            # a default (personal, empty owner) item — bob owns his personal scope
            res = store.remember(MemoryItem.create("mine", "note", id="p1"), assume_yes=True)
            self.assertTrue(res.committed)

    def test_undeterminable_grant_refuses_the_write_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            policy = _policy({"project": "not-a-dict"})          # malformed → deny on doubt
            store, _ = _store(d, identity="alice", access=policy)
            res = store.remember(self._proj_item("p1"), assume_yes=True)
            self.assertFalse(res.committed)
            self.assertIsNone(store.get("p1"))


# ============================================================ store — can_read filters reads
class TestReadFiltering(unittest.TestCase):
    def test_recall_filters_out_items_the_identity_cannot_see(self):
        with tempfile.TemporaryDirectory() as d:
            policy = _policy({"project": {VIEWER: ["bob"]}})     # bob: viewer at project only
            store, _ = _store(d, identity="bob", access=policy)
            # seed directly on the backend (bypassing the gate) to set up the read fixture
            store.backend.put(MemoryItem.create("p", "proj fact", id="vis",
                                                scope_level=PROJECT, scope_id="web"))
            store.backend.put(MemoryItem.create("s", "secret", id="cat",
                                                scope_level=CATEGORY, scope_id="secops"))
            store.backend.put(MemoryItem.create("a", "alice private", id="apriv",
                                                scope_level=PERSONAL, scope_id="alice"))
            visible = {i.id for i in store.scoped_active()}
            self.assertEqual(visible, {"vis"})                   # only the readable project item

    def test_local_no_policy_shows_everything_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            store, _ = _store(d)                                 # no access policy
            store.backend.put(MemoryItem.create("p", "x", id="a", scope_level=PROJECT,
                                                scope_id="web"))
            store.backend.put(MemoryItem.create("q", "y", id="b"))
            self.assertEqual({i.id for i in store.scoped_active()}, {"a", "b"})


# ============================================================ store — scope-promotion role gate (M-2)
class TestScopePromotion(unittest.TestCase):
    def _seed_personal(self, store, id="p1"):
        # seed a personal item on the backend (scope_id empty = legacy/owned)
        store.backend.put(MemoryItem.create("deploy-note", "deploy on fridays", id=id))
        return store.get(id)

    def test_promote_to_broader_scope_refused_without_promotion_approver(self):
        with tempfile.TemporaryDirectory() as d:
            policy = _policy({"team": {PROMOTION_APPROVER: ["lead"]}})
            store, led = _store(d, identity="bob", access=policy)   # bob is not promotion-approver
            self._seed_personal(store)
            res = store.promote_scope("p1", TEAM, assume_yes=True)
            self.assertIsInstance(res, ScopePromoteResult)
            self.assertFalse(res.changed)
            self.assertTrue(res.aborted)
            self.assertEqual(store.get("p1").scope_level, PERSONAL)   # unchanged
            self.assertEqual([e for e in led.entries()
                              if e["kind"] == "scope_promotion"], [])  # not ledgered

    def test_promote_to_broader_scope_allowed_and_ledgered_with_the_role(self):
        with tempfile.TemporaryDirectory() as d:
            policy = _policy({"team": {PROMOTION_APPROVER: ["lead"]}})
            store, led = _store(d, identity="lead", access=policy)
            self._seed_personal(store)
            res = store.promote_scope("p1", TEAM, to_id="acme", assume_yes=True, actor_id="lead")
            self.assertTrue(res.changed)
            self.assertEqual((res.old, res.new), (PERSONAL, TEAM))
            promoted = store.get("p1")
            self.assertEqual(promoted.scope_level, TEAM)
            self.assertEqual(promoted.scope_id, "acme")
            # ledgered (who / from→to / approval), routed through the WriteGate (P2)
            recs = [e for e in led.entries() if e["kind"] == "scope_promotion"]
            self.assertEqual(len(recs), 1)
            self.assertEqual((recs[0]["old"], recs[0]["new"], recs[0]["actor"]),
                             (PERSONAL, TEAM, "lead"))
            gate = [g for g in led.entries() if g["kind"] == "write_gate"]
            self.assertTrue(any(g["seq"] == recs[0]["approval_seq"]
                                and g["decision"] == "approved" for g in gate))

    def test_promotion_only_broadens_a_narrowing_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            policy = _policy({"team": {PROMOTION_APPROVER: ["lead"]}})
            store, _ = _store(d, identity="lead", access=policy)
            store.backend.put(MemoryItem.create("r", "team rule", id="t1",
                                                scope_level=TEAM, scope_id="acme"))
            res = store.promote_scope("t1", PERSONAL, assume_yes=True)   # narrowing
            self.assertFalse(res.changed)
            self.assertTrue(res.aborted)
            self.assertIn("broaden", res.message.lower())

    def test_declined_gate_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            policy = _policy({"team": {PROMOTION_APPROVER: ["lead"]}})
            store, led = _store(d, identity="lead", access=policy)
            self._seed_personal(store)
            before = store.stats.writes
            res = store.promote_scope("p1", TEAM, confirm=lambda _t: False)   # declines at gate
            self.assertFalse(res.changed)
            self.assertEqual(store.get("p1").scope_level, PERSONAL)
            self.assertEqual(store.stats.writes, before)
            self.assertEqual([e for e in led.entries()
                              if e["kind"] == "scope_promotion"], [])

    def test_scope_promotion_fail_closed_on_undeterminable_grant(self):
        with tempfile.TemporaryDirectory() as d:
            policy = _policy({"team": "not-a-dict"})            # malformed → deny on doubt
            store, _ = _store(d, identity="lead", access=policy)
            self._seed_personal(store)
            res = store.promote_scope("p1", TEAM, assume_yes=True)
            self.assertFalse(res.changed)
            self.assertEqual(store.get("p1").scope_level, PERSONAL)

    def test_local_no_policy_scope_promotion_allowed(self):
        # local/personal mode (no policy) — a single user has full authority; byte-identical path.
        with tempfile.TemporaryDirectory() as d:
            store, led = _store(d)                              # no access policy
            self._seed_personal(store)
            res = store.promote_scope("p1", TEAM, to_id="acme", assume_yes=True)
            self.assertTrue(res.changed)
            self.assertEqual(store.get("p1").scope_level, TEAM)


if __name__ == "__main__":
    unittest.main()
