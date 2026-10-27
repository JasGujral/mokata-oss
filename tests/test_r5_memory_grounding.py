"""R5 (0.0.26) — memory write grounding + `retire_when` + refresh verbs + write-time overlap.

Exit criterion 12: "memory write without grounding refused" — `test_R5_ungrounded_write_refused`
is the firing test. Each test is named for the behaviour it proves and goes red if that behaviour
is removed (firing controls are noted per test).

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata.config import Surface
from mokata.init import init_repo
from mokata.memory import MemoryItem, MemoryStore
from mokata.memory.grounding import (BASIS_NO_GRAPH, check_grounding, evaluate_retire_when,
                                     extract_value_citations)
from mokata.memory.healing import RETIRE_DUE
from mokata.memory.item import ACTIVE, PERSISTENT, SUPERSEDED


def _git(root, *args):
    subprocess.run(["git", "-C", root, *args], check=True, capture_output=True, text=True)


def _repo():
    d = tempfile.mkdtemp(prefix="mokata-r5-")
    with redirect_stdout(io.StringIO()):
        init_repo(root=d, profile="full", assume_yes=True, out=lambda *a, **k: None)
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    with open(os.path.join(d, "src", "billing.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("def charge():\n    return 1\n\n\ndef refund():\n    return 0\n")
    _git(d, "init", "-q")
    _git(d, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed")
    sha = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()
    return d, sha


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.root, self.sha = _repo()
        self.store = MemoryStore.from_surface(Surface.load(self.root))
        self.store.knowledge_layer = None        # the floor: no authoritative graph

    def remember(self, subject, value, **kw):
        item = MemoryItem.create(subject, value, mtype=PERSISTENT, **kw)
        return self.store.remember(item, assume_yes=True)


class TestR5Grounding(_Fixture):
    def test_R5_ungrounded_write_refused(self):
        """A learning citing a file that does not exist is REFUSED, the refusal names the
        citation, and nothing is stored. (Firing control: drop the grounding check in
        `MemoryStore.remember` and `committed` turns True.)"""
        res = self.remember("billing rule", "refunds live in src/payments/refund.py:12")
        self.assertFalse(res.committed)
        self.assertTrue(res.refused)
        self.assertIn("src/payments/refund.py:12", res.message)
        self.assertIn("no such file", res.message)
        self.assertEqual([i for i in self.store.backend.all() if i.subject == "billing rule"], [])

    def test_R5_grounded_write_accepted(self):
        res = self.remember("billing rule",
                            f"refund is defined in src/billing.py:5 since commit {self.sha[:10]}; "
                            "see https://example.com/docs",
                            about_code=["src/billing.py"])
        self.assertTrue(res.committed, res.message)

    def test_R5_line_anchor_past_eof_refused(self):
        res = self.remember("billing rule", "see src/billing.py:99")
        self.assertTrue(res.refused)
        self.assertIn("line 99", res.message)

    def test_R5_unknown_commit_refused(self):
        res = self.remember("billing rule", "fixed in commit deadbeefdeadbeef")
        self.assertTrue(res.refused)
        self.assertIn("deadbeefdeadbeef", res.message)

    def test_R5_malformed_link_refused_without_network(self):
        res = self.remember("billing rule", "spec at https://")
        self.assertTrue(res.refused)
        self.assertIn("link", res.message)

    def test_R5_about_code_path_refused_when_missing(self):
        res = self.remember("billing rule", "refunds are idempotent",
                            about_code=["src/gone.py"])
        self.assertTrue(res.refused)
        self.assertIn("src/gone.py", res.message)

    def test_R5_symbol_without_graph_is_accepted_and_disclosed(self):
        item = MemoryItem.create("billing rule", "refunds are idempotent",
                                 about_code=["BillingService.refund"])
        rep = self.store.grounding_report(item)
        self.assertTrue(rep.ok)
        self.assertIn(BASIS_NO_GRAPH, "\n".join(rep.lines()))

    def test_R5_symbol_refused_when_graph_says_absent(self):
        class _Primary:
            name = "CRG"
            supports_resolve = True

            def resolves(self, sym):
                return sym == "charge"

        class _Layer:
            primary = _Primary()

        item = MemoryItem.create("x", "y", about_code=["refund_all"])
        rep = check_grounding(item, self.root, _Layer())
        self.assertFalse(rep.ok)
        self.assertIn("refund_all", rep.refusal())
        ok = check_grounding(MemoryItem.create("x", "y", about_code=["charge"]), self.root,
                             _Layer())
        self.assertTrue(ok.ok)

    def test_R5_path_escaping_repo_refused(self):
        res = self.remember("x", "y", about_code=["../outside.py"])
        self.assertTrue(res.refused)
        self.assertIn("escapes", res.message)

    def test_R5_prose_is_not_mistaken_for_citations(self):
        cites = extract_value_citations("a/b testing, and/or TCP/IP at 50 km/h; v1.2/3.4")
        self.assertEqual(cites, [])

    def test_R5_episodic_turns_are_not_grounded(self):
        from mokata.memory.item import EPISODIC
        item = MemoryItem.create("turn", "user mentioned src/nope.py:3", mtype=EPISODIC)
        self.assertTrue(self.store.grounding_report(item).ok)

    def test_R5_grounding_never_walks_the_repo(self):
        import mokata.memory.grounding as G
        calls = []
        real = os.walk
        try:
            os.walk = lambda *a, **k: calls.append(a) or real(*a, **k)   # noqa: E731
            self.store.grounding_report(MemoryItem.create("x", "see src/billing.py:2"))
        finally:
            os.walk = real
        self.assertEqual(calls, [])
        self.assertFalse(hasattr(G, "walk"))

    def test_R5_private_span_excluded_from_storage(self):
        res = self.remember("deploy", "use the bastion <private>pw is hunter2</private> host")
        self.assertTrue(res.committed, res.message)
        stored = self.store.get(res.item.id)
        self.assertNotIn("hunter2", stored.value)
        self.assertIn("[private", stored.value)

    def test_R5_unbalanced_private_tag_refused(self):
        res = self.remember("deploy", "use <private>the secret host")
        self.assertTrue(res.refused)


class TestR5RetireWhen(_Fixture):
    def _land(self, **kw):
        res = self.remember("billing rule", "refunds go through refund()",
                            about_code=["src/billing.py"], **kw)
        self.assertTrue(res.committed, res.message)
        return res.item

    def test_R5_retire_when_fires_after_cited_file_deleted(self):
        """(Firing control: drop `retire_due=` from `MemoryStore.detect_issues` and no RETIRE_DUE
        proposal appears.)"""
        item = self._land(retire_when=["deleted:src/billing.py"])
        self.assertEqual([p for p in self.store.detect_issues() if p.kind == RETIRE_DUE], [])
        os.remove(os.path.join(self.root, "src", "billing.py"))
        due = [p for p in self.store.detect_issues() if p.kind == RETIRE_DUE]
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0].old.id, item.id)
        self.assertEqual(due[0].predicate, "deleted:src/billing.py")
        self.assertIn("no longer exists", due[0].rationale)
        # Surfacing changed nothing: the learning is still active.
        self.assertEqual(self.store.get(item.id).status, ACTIVE)

    def test_R5_retire_when_date_and_after_commit(self):
        self._land(retire_when=["date:2020-01-01", f"after:{self.sha}"])
        fired = {p.predicate for p in self.store.detect_issues() if p.kind == RETIRE_DUE}
        self.assertEqual(fired, {"date:2020-01-01", f"after:{self.sha}"})

    def test_R5_retire_when_is_grounded_at_write(self):
        res = self.remember("x", "y", retire_when=["deleted:src/never.py"])
        self.assertTrue(res.refused)
        self.assertIn("fire immediately", res.message)
        res = self.remember("x", "y", retire_when=["someday:soon"])
        self.assertTrue(res.refused)
        res = self.remember("x", "y", retire_when=["date:not-a-date"])
        self.assertTrue(res.refused)

    def test_R5_evaluator_has_no_opinion_without_evidence(self):
        item = MemoryItem.create("x", "y", retire_when=["deleted:src/x.py", "after:abc1234",
                                                        "removed:Foo"])
        self.assertEqual(evaluate_retire_when([item], root="", layer=None), [])

    def _due(self):
        os.remove(os.path.join(self.root, "src", "billing.py"))
        return [p for p in self.store.detect_issues() if p.kind == RETIRE_DUE][0]

    def test_R5_refresh_verbs_need_approval(self):
        """Every verb goes through the human gate: a declined gate changes NOTHING. (Firing
        control: commit before `_gated_commit` in `refresh` and the item changes on decline.)"""
        from mokata.memory.healing import REFRESH_VERBS
        item = self._land(retire_when=["deleted:src/billing.py"])
        other = self.remember("ledger rule", "ledger entries are append-only").item
        p = self._due()
        for verb in REFRESH_VERBS:
            asked = []
            kw = {"value": "refunds now go through the ledger"} \
                if verb in ("update", "consolidate", "replace") else {}
            if verb in ("update", "replace"):
                kw["about_code"] = []        # the old anchor is gone — it would be ungrounded
            if verb == "consolidate":
                kw["into_id"] = other.id
            res = self.store.refresh(p, verb, confirm=lambda t: asked.append(t) or False, **kw)
            self.assertFalse(res.changed, verb)
            self.assertEqual(len(asked), 1, f"{verb} did not ask the human")
            self.assertIn(verb.upper(), asked[0])
            now = self.store.get(item.id)
            self.assertIsNotNone(now, verb)
            self.assertEqual((now.status, now.value, now.retire_when),
                             (ACTIVE, "refunds go through refund()", ["deleted:src/billing.py"]),
                             verb)

    def test_R5_refresh_keep_drops_fired_predicate(self):
        item = self._land(retire_when=["deleted:src/billing.py", "date:2999-01-01"])
        res = self.store.refresh(self._due(), "keep", assume_yes=True)
        self.assertTrue(res.changed)
        self.assertEqual(self.store.get(item.id).retire_when, ["date:2999-01-01"])
        self.assertEqual([p for p in self.store.detect_issues() if p.kind == RETIRE_DUE], [])

    def test_R5_refresh_update_in_place_is_grounded(self):
        item = self._land(retire_when=["deleted:src/billing.py"])
        p = self._due()
        bad = self.store.refresh(p, "update", value="now in src/billing.py:3", assume_yes=True)
        self.assertTrue(bad.refused)                 # the file is gone — the update is ungrounded
        res = self.store.refresh(p, "update", value="refunds moved to the ledger service",
                                 about_code=[], assume_yes=True)
        self.assertTrue(res.changed, res.message)
        now = self.store.get(item.id)
        self.assertEqual(now.value, "refunds moved to the ledger service")
        self.assertEqual(now.retire_when, [])

    def test_R5_refresh_replace_supersedes(self):
        item = self._land(retire_when=["deleted:src/billing.py"])
        res = self.store.refresh(self._due(), "replace", value="refunds go via the ledger",
                                 about_code=[], assume_yes=True)
        self.assertTrue(res.changed)
        self.assertEqual(self.store.get(item.id).status, SUPERSEDED)
        self.assertIn(item.id, self.store.get(res.item.id).supersedes)

    def test_R5_refresh_consolidate_folds_into_target(self):
        item = self._land(retire_when=["deleted:src/billing.py"])
        other = self.remember("ledger rule", "ledger entries are append-only").item
        res = self.store.refresh(self._due(), "consolidate", into_id=other.id,
                                 value="ledger entries are append-only; refunds are entries",
                                 assume_yes=True)
        self.assertTrue(res.changed)
        self.assertEqual(self.store.get(item.id).status, SUPERSEDED)
        tgt = self.store.get(other.id)
        self.assertIn("refunds are entries", tgt.value)
        self.assertIn(item.id, tgt.supersedes)

    def test_R5_refresh_delete_removes_row(self):
        item = self._land(retire_when=["deleted:src/billing.py"])
        res = self.store.refresh(self._due(), "delete", assume_yes=True)
        self.assertTrue(res.changed)
        self.assertIsNone(self.store.get(item.id))

    def test_R5_generic_approve_cannot_retire(self):
        from mokata.memory.store import MemoryError as StoreMemoryError
        self._land(retire_when=["deleted:src/billing.py"])
        with self.assertRaises(StoreMemoryError):
            self.store.apply_proposal(self._due(), "approve", assume_yes=True)


class TestR5Overlap(_Fixture):
    def test_R5_overlap_updates_existing_instead_of_duplicating(self):
        """A 4–5/5 overlap lands ON the existing entry (same id) — no near-duplicate row.
        (Firing control: drop the `ov.updates` branch in `remember` and two rows appear.)"""
        first = self.remember("refund policy", "refunds are issued within 5 business days").item
        seen = []
        item = MemoryItem.create("Refund Policy", "refunds are issued within 7 business days",
                                 mtype=PERSISTENT)
        res = self.store.remember(item, confirm=lambda t: seen.append(t) or True)
        self.assertTrue(res.committed)
        self.assertTrue(res.updated_existing)
        self.assertEqual(res.item.id, first.id)
        self.assertIn("UPDATES the existing memory", seen[0])
        rows = [i for i in self.store.backend.all() if "refund" in i.subject.lower()]
        self.assertEqual(len(rows), 1)
        self.assertIn("7 business days", rows[0].value)

    def test_R5_partial_overlap_is_flagged_not_merged(self):
        self.remember("refund policy", "refunds are issued within 5 business days")
        seen = []
        item = MemoryItem.create("refund policy", "card chargebacks go to the risk team",
                                 mtype=PERSISTENT)
        res = self.store.remember(item, confirm=lambda t: seen.append(t) or True)
        self.assertTrue(res.committed)
        self.assertFalse(res.updated_existing)
        self.assertIn("consider consolidating", seen[0])
        self.assertTrue(res.overlap.startswith("3/5"))

    def test_R5_unrelated_write_has_no_overlap(self):
        self.remember("refund policy", "refunds are issued within 5 business days")
        res = self.remember("deploy window", "deploys happen on tuesdays")
        self.assertEqual(res.overlap, "")


class TestR5McpSurface(_Fixture):
    def test_R5_mcp_remember_refuses_ungrounded_before_any_proposal(self):
        """The MCP write path refuses at PROPOSE time: no proposal is minted for a human to
        rubber-stamp. (Firing control: drop the grounding block in `tools_memory.remember` and the
        status becomes 'proposed'.)"""
        from mokata import approval
        from mokata.mcp.tools_write import remember
        before = len(approval.pending(self.root))
        out = remember(path=self.root, subject="pricing", value="charge in src/ghost.py:4")
        self.assertEqual(out["status"], "refused")
        self.assertFalse(out["committed"])
        self.assertEqual(out["unresolved"], ["src/ghost.py:4"])
        self.assertEqual(len(approval.pending(self.root)), before)

    def test_R5_mcp_remember_grounded_is_proposed_with_basis(self):
        from mokata.mcp.tools_write import remember
        out = remember(path=self.root, subject="pricing", value="charge in src/billing.py:1",
                       retire_when=["deleted:src/billing.py"])
        self.assertEqual(out["status"], "proposed")
        self.assertFalse(out["committed"])
        self.assertTrue(any("src/billing.py:1" in g for g in out["grounding"]))

    def test_R5_mcp_refresh_verb_is_propose_only(self):
        from mokata.mcp.tools_write import apply_proposal
        item = self.remember("billing rule", "refunds go through refund()",
                             retire_when=["deleted:src/billing.py"]).item
        os.remove(os.path.join(self.root, "src", "billing.py"))
        out = apply_proposal(path=self.root, subject="billing rule", decision="delete")
        self.assertEqual(out["status"], "proposed")
        self.assertIsNotNone(self.store.get(item.id))      # nothing applied without approval


class TestR5Cli(_Fixture):
    def test_R5_cli_refresh_lists_then_declines_without_yes(self):
        from mokata.cli import main
        item = self.remember("billing rule", "refunds go through refund()",
                             retire_when=["deleted:src/billing.py"]).item
        os.remove(os.path.join(self.root, "src", "billing.py"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["memory", "refresh", "--path", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("deleted:src/billing.py", buf.getvalue())
        # The decline comes from the real reader on a non-tty stdin double (a36: never the
        # ambient stdin) — the gate refuses without an explicit yes.
        with redirect_stdout(io.StringIO()), _support.stdin_is(_support.NoTtyStdin()):
            rc = main(["memory", "refresh", "billing rule", "--verb", "delete",
                       "--path", self.root])
        self.assertEqual(rc, 1)
        self.assertIsNotNone(self.store.get(item.id))
        with redirect_stdout(io.StringIO()):
            rc = main(["memory", "refresh", "billing rule", "--verb", "delete", "--yes",
                       "--path", self.root])
        self.assertEqual(rc, 0)
        self.assertIsNone(self.store.get(item.id))


if __name__ == "__main__":
    unittest.main()
