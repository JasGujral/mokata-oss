"""0.0.29 lane G — the spec leg is valid for the decisions it read, BY CONTENT, at any age.

`SPEC-LEG-DECISIONS-UNFINGERPRINTED` + `SPEC-LEG-AGE-BOUND-DISABLED-AT-ZERO` (doc 84). Design:
`docs/build/handoff/0029-design/G-gates-mcp-recording.md` §3.4 (+ §9 M4). Real git repos and a REAL
linked worktree (`git worktree add`, P31's fixture through P21's `_Merge`), never a mock store.
Labels: **force** = red on the base (`9045ad7`), **guard** = pins behaviour that already held.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""
from __future__ import annotations

import os
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from test_f_spec_leg_in_merge_gate import _Leg
from mokata import merge_gate as MG
from mokata import merge_spec as MSL
from mokata.config import Surface
from mokata.memory import DECISION, MemoryStore


def _store(wt):
    return MemoryStore.from_surface(Surface.load(wt))


def _decide(wt, subject, value="use postgres"):
    res = _store(wt).remember_decision(subject, value, assume_yes=True)
    return res


def _one(wt, subject):
    items = [i for i in _store(wt).peek_active(mtype=DECISION) if i.subject == subject]
    assert len(items) == 1, items
    return items[0]


class DecisionsByContent(_Leg):

    def setUp(self):
        super().setUp()
        self.wt = self.quiet()
        _decide(self.wt, "db.engine")
        self.evidence(self.wt)
        self.assertEqual(set(), self.codes(self.wt), MG.plan_merge(self.main, self.wt).render())

    def stale_reason(self):
        plan = MG.plan_merge(self.main, self.wt)
        rows = [b for b in plan.blocked if b["code"] == MG.SPEC_STALE]
        return rows[0]["text"] if rows else ""

    def test_the_record_carries_the_fingerprint_taken_from_the_worktree(self):
        """force — the record holds `{id: hash}` for the decisions the leg read."""
        rec = self.rec()
        self.assertIsInstance(rec.get("decisions"), dict)
        self.assertEqual(MSL.decisions_fp(self.wt), rec["decisions"])
        self.assertEqual(1, len(rec["decisions"]))

    def test_a_decision_recorded_since_is_stale_with_the_count(self):
        """force — a new decision after the leg ran: stale, "1 decision(s)"."""
        _decide(self.wt, "cache.policy", "write-through")
        reason = self.stale_reason()
        self.assertIn("1 decision(s) recorded, changed or retired since they ran", reason)
        self.assertIn("mokata worktree test", reason)

    def test_any_field_edited_is_stale(self):
        """force — `to_dict` in full: value, enforcement, scope, expiry — each alone moves it."""
        edits = {
            "value": lambda i: setattr(i, "value", "use sqlite"),
            "enforcement": lambda i: setattr(i, "enforcement", "block"),
            "expires_at": lambda i: setattr(i, "expires_at", "2099-01-01T00:00:00+00:00"),
            "supersedes": lambda i: i.supersedes.append("someone-else"),
        }
        for name, edit in edits.items():
            with self.subTest(name):
                store = _store(self.wt)
                item = _one(self.wt, "db.engine")
                before = item.to_dict()
                edit(item)
                self.assertNotEqual(before, item.to_dict(), f"{name} is not in to_dict")
                store.backend.update(item)
                self.assertIn("1 decision(s)", self.stale_reason(), name)
                self.evidence(self.wt)                    # re-run: fresh again
                self.assertEqual("", self.stale_reason())

    def test_a_retired_decision_is_stale(self):
        """force — a decision that stops being active (superseded) is "retired since"."""
        from mokata.memory import SUPERSEDED
        store = _store(self.wt)
        item = _one(self.wt, "db.engine")
        item.status = SUPERSEDED
        store.backend.update(item)
        self.assertIn("1 decision(s)", self.stale_reason())

    def test_old_but_unmoved_is_not_stale(self):
        """force — 25 h old and nothing moved: valid (no age bound on the spec legs any more)."""
        self.put(dict(self.rec(), at=time.time() - 25 * 3600))
        self.assertEqual(set(), self.codes(self.wt), MG.plan_merge(self.main, self.wt).render())

    def test_an_unreadable_store_is_unknown_and_blocks(self):
        """force — the judge cannot read the decisions: UNKNOWN, which never passes."""
        with mock.patch.object(MSL, "decisions_fp", side_effect=OSError("gone")):
            plan = MG.plan_merge(self.main, self.wt)
        self.assertFalse(plan.ok)
        self.assertIn("unknown-evidence", {b["code"] for b in plan.blocked})
        self.assertIn("the decision store could not be read (OSError)", plan.reason())

    def test_a_record_without_the_map_is_stale(self):
        """force — a record that does not say which decisions it read never passes; an EMPTY map
        (a repo with no decisions) is a valid map."""
        rec = self.rec()
        for bad in (None, [], "x"):
            with self.subTest(bad=bad):
                r = dict(rec)
                if bad is None:
                    r.pop("decisions")
                else:
                    r["decisions"] = bad
                self.put(r)
                self.assertIn("does not say which decisions", self.stale_reason())

    def test_the_fingerprint_read_counts_no_read(self):
        """force — taking the fingerprint bumps no read counter (`peek_active`, not `all_active`)."""
        before = _store(self.wt).stats.reads
        MSL.decisions_fp(self.wt)
        MSL.decisions_fp(self.wt)
        self.assertEqual(before, _store(self.wt).stats.reads)

    def test_ten_thousand_decisions_are_one_query_and_linear(self):
        """force (scale; red on the base because `decisions_fp` is new) — 10^4 decisions: one
        `peek_active` call per fingerprint.

        The rows land through `_scale_fixture.load_sqlite`: `SQLiteBackend.put`'s own body
        (`_put_on`) with the connection and the commit hoisted, so the INSERT is the one users run
        and only the per-row commit is gone. 0.0.29 GF: 10^4 `put`s were 10^4 commits, and a commit
        on the Windows runner's disk made this one cell ~9 of the module's ~10 minutes there
        (`windows · jsonschema=absent · shard 4/4` over its CI-CEILING, run 43896526038). The
        write path is not what this cell proves; the fingerprint read is."""
        from types import SimpleNamespace
        from _scale_fixture import load_sqlite
        from mokata.memory import MemoryItem
        store = _store(self.wt)
        rows = [MemoryItem.create(f"d.{i}", f"v{i}", mtype=DECISION) for i in range(10_000)]
        self.assertEqual(10_000, load_sqlite(store.backend, SimpleNamespace(items=rows)))
        calls = []
        real = MemoryStore.peek_active

        def spy(self_, *a, **kw):
            calls.append(1)
            return real(self_, *a, **kw)
        with mock.patch.object(MemoryStore, "peek_active", spy):
            t0 = time.perf_counter()
            fp = MSL.decisions_fp(self.wt)
            ms = (time.perf_counter() - t0) * 1000.0
        import sys
        sys.stderr.write(f"\n[G] decisions_fp over 10^4 decisions: {ms:.0f} ms\n")
        self.assertEqual(1, len(calls))
        self.assertEqual(10_001, len(fp))


class FingerprintBeforeTheLeg(_Leg):

    def test_a_decision_written_while_the_leg_runs_is_stale(self):
        """force — the fingerprint is taken BEFORE the leg reads decisions: one written during
        the leg is in neither, and the judge sees it as moved."""
        wt = self.quiet()
        from mokata import ci_check as CI
        real = CI.run_ci_check

        def during(*a, **kw):
            _decide(wt, "late.decision", "written while the leg ran")
            return real(*a, **kw)
        with mock.patch.object(CI, "run_ci_check", during):
            self.evidence(wt)
        self.assertIn(MG.SPEC_STALE, self.codes(wt))


class Provenance(_Leg):

    def test_the_record_carries_source_and_actor(self):
        """force — like the suite record: `source: runner`, `actor` the IDE actor."""
        from mokata.ide.review_context import ide_actor
        wt = self.quiet()
        self.evidence(wt)
        rec = self.rec()
        self.assertEqual("runner", rec.get("source"))
        self.assertEqual(ide_actor(), rec.get("actor"))

    def test_a_future_at_changes_only_the_cite(self):
        """guard — `at` is a label, never a validity input."""
        wt = self.quiet()
        self.evidence(wt)
        self.put(dict(self.rec(), at=time.time() + 10 * 24 * 3600))
        self.assertEqual(set(), self.codes(wt))


if __name__ == "__main__":
    unittest.main()
