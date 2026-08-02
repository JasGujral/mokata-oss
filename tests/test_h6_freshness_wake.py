"""H-6 S2 — the WAKE: `fingerprint_forces_refresh` becomes a FOURTH freshness signal.

GR.S4 left a NAMED, DORMANT hook at `knowledge/freshness.py:531` with an explicit note that 0.0.16
(H-6) would supply the recorded-vs-current fingerprints and wire it into `_reconcile`. S1 built the
durable record that supplies them. This slice is the wake.

The claim being made is GR.S4's own, applied to a new input: **a graph KNOWN stale never answers.**
An `about_code` anchor whose file has moved since it was recorded is exactly that knowledge — so it
joins the dirty-set, the HEAD probe and the cold walk as a reason to rebuild BEFORE answering.

What is pinned here:

  P1  ONE TRIPWIRE, REACHED. The comparison lives in `anchor_fingerprints`, and `_reconcile`
      consumes its verdict rather than re-deriving one. Without it there are two fingerprint
      comparisons in the codebase and they will drift.
  S2a THE SIGNAL IS NOT DRAINED, SO IT NEEDS A FORCED-LEDGER. Unlike the dirty-set (drained), HEAD
      (advanced) and the cold walk (once), the durable record is deliberately NOT re-stamped by a
      rebuild — S3/S4 depend on it staying un-restamped until a HUMAN decides. Without a per-session
      memory of what has already been forced, one moved anchor rebuilds the graph on EVERY query
      for the rest of the session.
  S2b BOUNDED, AND IT SAYS SO. Past the anchor-scan cap the pass is bounded with an honest costed
      note — never a block, never a silent truncation (the existing `FRESHNESS_CHANGE_CAP`
      precedent).
  S2c NO RECORD ⇒ BYTE-IDENTICAL TO PRE-H-6. A repo with no anchors recorded pays nothing it did
      not already pay, and the warm-path perf contract still holds.
  S2d THE RECONCILE ACQUIRES NO GRAPH READ. `_reconcile` decides whether to rebuild the graph; a
      version of it that QUERIED the graph to make that decision is a loop, not a signal.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import tempfile
import time
import unittest

import _support  # noqa: F401

from mokata.knowledge import anchor_fingerprints as AF
from mokata.knowledge import freshness as F


class _Primary:
    """A graph double that RECORDS every query — S2d's instrument."""

    is_graph = True

    def __init__(self):
        self.queries = []
        self.refreshed = 0

    def supports_kind(self, kind):
        return True

    def query(self, kind, target, depth=1):
        self.queries.append((kind, target))
        raise AssertionError("_reconcile must not query the graph")

    def refresh(self, root, full=False):
        self.refreshed += 1
        return True


class _Layer:
    def __init__(self, primary=None):
        self.primary = primary


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".mokata"), exist_ok=True)

    def write(self, rel, text):
        ab = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(ab), exist_ok=True)
        with open(ab, "w", encoding="utf-8") as fh:
            fh.write(text)

    def controller(self, sid="s1"):
        return F.FreshnessController(self.root, session_id=sid)

    def settled(self, ctrl, layer):
        """Run one reconcile so the cold walk is done and the baseline is seeded."""
        ctrl.ensure_fresh(layer)
        return ctrl.ensure_fresh(layer)


# ================================================================ the wake itself
class Wake(_Base):

    def test_a_moved_anchor_forces_a_rebuild(self):
        self.write("pkg/mod.py", "def f():\n    return 1\n")
        AF.record_anchors(self.root, ["pkg/mod.py"])
        ctrl, layer = self.controller(), _Layer(_Primary())
        out = self.settled(ctrl, layer)
        self.assertTrue(out.fresh)                          # settled: nothing pending

        self.write("pkg/mod.py", "def f():\n    return 2\n")
        # Drain the dirty-set/HEAD routes out of the picture: this file changed OUT OF BAND, with
        # no hook, no commit, and a cold walk already done. Pre-H-6 that is invisible until the
        # post-answer recheck; the anchor record is what makes it a BEFORE-answer signal.
        out = ctrl.ensure_fresh(layer)
        self.assertFalse(out.fresh)
        self.assertIn("pkg/mod.py", out.changed)
        self.assertTrue(out.rebuilt)

    def test_an_unmoved_anchor_is_silent(self):
        self.write("pkg/mod.py", "def f():\n    return 1\n")
        AF.record_anchors(self.root, ["pkg/mod.py"])
        ctrl, layer = self.controller(), _Layer(_Primary())
        out = self.settled(ctrl, layer)
        self.assertTrue(out.fresh)
        self.assertFalse(out.rebuilt)

    def test_an_unrecorded_anchor_is_silent(self):
        # P6 reaching the freshness lane: no baseline is no opinion, so an anchored file that was
        # never recorded is not a reason to rebuild.
        self.write("pkg/mod.py", "def f():\n    return 1\n")
        ctrl, layer = self.controller(), _Layer(_Primary())
        self.settled(ctrl, layer)
        self.write("pkg/mod.py", "def f():\n    return 2\n")
        self.assertTrue(ctrl.ensure_fresh(layer).fresh)


# ================================================================ S2a — the forced-ledger
class ForcedOnce(_Base):

    def test_one_move_forces_exactly_one_rebuild(self):
        self.write("pkg/mod.py", "v = 1\n")
        AF.record_anchors(self.root, ["pkg/mod.py"])
        ctrl, primary = self.controller(), _Primary()
        layer = _Layer(primary)
        self.settled(ctrl, layer)

        self.write("pkg/mod.py", "v = 2\n")
        first = ctrl.ensure_fresh(layer)
        self.assertTrue(first.rebuilt)
        after_one = primary.refreshed

        # ...and the SAME unresolved staleness must not rebuild again on every later query.
        for _ in range(3):
            later = ctrl.ensure_fresh(layer)
            self.assertTrue(later.fresh)
            self.assertFalse(later.rebuilt)
        self.assertEqual(after_one, primary.refreshed)

    def test_the_durable_record_is_NOT_restamped_by_a_rebuild(self):
        # P7, and the reason S2a needs a separate ledger at all: S3's proposal and S4's refusal
        # both read this record, and a rebuild silently marking the anchor current would delete
        # the very evidence they exist to raise.
        self.write("pkg/mod.py", "v = 1\n")
        AF.record_anchors(self.root, ["pkg/mod.py"])
        before = AF.read_record(self.root)["pkg/mod.py"]["fingerprint"]
        ctrl, layer = self.controller(), _Layer(_Primary())
        self.settled(ctrl, layer)
        self.write("pkg/mod.py", "v = 2\n")
        ctrl.ensure_fresh(layer)
        ctrl.ensure_fresh(layer)
        self.assertEqual(before, AF.read_record(self.root)["pkg/mod.py"]["fingerprint"])
        self.assertEqual(AF.MOVED, AF.evaluate_anchor("pkg/mod.py", root=self.root).verdict)

    def test_a_second_move_forces_again(self):
        self.write("pkg/mod.py", "v = 1\n")
        AF.record_anchors(self.root, ["pkg/mod.py"])
        ctrl, layer = self.controller(), _Layer(_Primary())
        self.settled(ctrl, layer)
        self.write("pkg/mod.py", "v = 2\n")
        self.assertTrue(ctrl.ensure_fresh(layer).rebuilt)
        self.assertFalse(ctrl.ensure_fresh(layer).rebuilt)
        self.write("pkg/mod.py", "v = 3\n")
        self.assertTrue(ctrl.ensure_fresh(layer).rebuilt)      # a NEW move is a new signal

    def test_a_new_session_forces_once_more(self):
        # A fresh session has not established what its graph reflects — the cold walk re-runs for
        # exactly this reason, and the forced-ledger is session-scoped for the same one.
        self.write("pkg/mod.py", "v = 1\n")
        AF.record_anchors(self.root, ["pkg/mod.py"])
        layer = _Layer(_Primary())
        c1 = self.controller("s1")
        self.settled(c1, layer)
        self.write("pkg/mod.py", "v = 2\n")
        self.assertTrue(c1.ensure_fresh(layer).rebuilt)
        self.assertFalse(c1.ensure_fresh(layer).rebuilt)

        # The DISCRIMINATOR: session 2 must force for the SAME unresolved state session 1 already
        # forced for. Anything weaker (asserting a rebuild after a FURTHER edit) passes just as
        # happily with a process-global ledger, and the first version of this test did.
        c2 = self.controller("s2")
        self.assertTrue(c2.ensure_fresh(layer).rebuilt)
        self.assertFalse(c2.ensure_fresh(layer).rebuilt)        # ...and then settles


# ================================================================ S2b — bounded, and it says so
class Bounded(_Base):

    def test_past_the_cap_it_is_costed_not_blocked(self):
        anchors = []
        for i in range(AF.ANCHOR_SCAN_CAP + 5):
            rel = f"pkg/a_{i}.py"
            self.write(rel, f"v = {i}\n")
            anchors.append(rel)
        AF.record_anchors(self.root, anchors)
        ctrl, layer = self.controller(), _Layer(_Primary())
        self.settled(ctrl, layer)

        out = ctrl.ensure_fresh(layer)
        self.assertTrue(out.capped)
        self.assertIn("anchor", out.note)
        self.assertIn("5", out.note)              # names how many were NOT checked

    def test_under_the_cap_there_is_no_note(self):
        self.write("pkg/a.py", "v = 1\n")
        AF.record_anchors(self.root, ["pkg/a.py"])
        ctrl, layer = self.controller(), _Layer(_Primary())
        out = self.settled(ctrl, layer)
        self.assertNotIn("anchor", out.note)


# ================================================================ S2c/S2d — boundaries
class Boundaries(_Base):

    def test_no_record_is_byte_identical_to_pre_h6(self):
        self.write("pkg/mod.py", "v = 1\n")
        ctrl, layer = self.controller(), _Layer(_Primary())
        out = self.settled(ctrl, layer)
        self.assertTrue(out.fresh)
        self.assertEqual("", out.note)
        self.assertFalse(out.capped)
        self.assertFalse(os.path.exists(AF.record_path(self.root)))

    def test_reconcile_never_queries_the_graph(self):
        # The `_Primary` double RAISES on any query; a reconcile that asked would blow up rather
        # than quietly pass. Symbol anchors are the only thing that could ask, so one is recorded.
        self.write("pkg/mod.py", "v = 1\n")
        AF.record_anchors(self.root, ["pkg/mod.py"])
        rec = AF.read_record(self.root)
        rec["Some.Symbol"] = {"shape": AF.SHAPE_SYMBOL, "fingerprint": "aaa", "path": "pkg/mod.py"}
        AF._write_record(self.root, rec)

        ctrl, primary = self.controller(), _Primary()
        layer = _Layer(primary)
        self.settled(ctrl, layer)
        self.write("pkg/mod.py", "v = 2\n")
        ctrl.ensure_fresh(layer)
        self.assertEqual([], primary.queries)

    def test_the_record_is_not_written_by_a_reconcile(self):
        self.write("pkg/mod.py", "v = 1\n")
        AF.record_anchors(self.root, ["pkg/mod.py"])
        with open(AF.record_path(self.root), "rb") as fh:
            before = fh.read()
        ctrl, layer = self.controller(), _Layer(_Primary())
        self.settled(ctrl, layer)
        self.write("pkg/mod.py", "v = 2\n")
        ctrl.ensure_fresh(layer)
        with open(AF.record_path(self.root), "rb") as fh:
            self.assertEqual(before, fh.read())

    def test_a_broken_record_never_breaks_a_reconcile(self):
        self.write("pkg/mod.py", "v = 1\n")
        AF.record_anchors(self.root, ["pkg/mod.py"])
        with open(AF.record_path(self.root), "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        ctrl, layer = self.controller(), _Layer(_Primary())
        self.assertTrue(self.settled(ctrl, layer).fresh)

    def test_an_exploding_anchor_signal_never_costs_a_query_its_answer(self):
        # The boundary the handler in `FreshnessController._anchor_signal` actually guards. A
        # corrupt record file does NOT reach it (`read_record` answers `{}`), so it has to be
        # raised on purpose — otherwise the handler is registered, unexercised and untrusted.
        self.write("pkg/mod.py", "v = 1\n")
        AF.record_anchors(self.root, ["pkg/mod.py"])
        original = AF.anchor_signal

        def boom(*a, **k):
            raise RuntimeError("anchor signal exploded")

        AF.anchor_signal = boom
        self.addCleanup(setattr, AF, "anchor_signal", original)
        ctrl, layer = self.controller(), _Layer(_Primary())
        self.settled(ctrl, layer)

        # THE DISCRIMINATOR: "it still answers" is NOT the contract — `ensure_fresh`'s own D5
        # handler already guarantees that, and it would swallow this too. What that outer handler
        # cannot do is finish the reconcile: it abandons a dirty-set that `drain_dirty` has ALREADY
        # consumed, so the edit it named is lost. The inner handler exists so the other three
        # signals still land, and this is the only assertion that can tell the two apart.
        self.write("pkg/other.py", "v = 2\n")
        F.mark_dirty(self.root, ["pkg/other.py"], session_id="s1")
        out = ctrl.ensure_fresh(layer)
        self.assertTrue(out.rebuilt)
        self.assertIn("pkg/other.py", out.changed)


# ================================================================ cost tracks anchors, not repo
class Cost(_Base):

    def test_warm_cost_does_not_scale_with_repo_size(self):
        # The GR.S4 perf contract (cost ∝ churn, never repo size) restated for the new signal: the
        # anchor pass is bounded by the ANCHOR COUNT, and a 30x larger repo with the same anchors
        # pays the same.
        def repo(n):
            root = tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, root, ignore_errors=True)
            os.makedirs(os.path.join(root, ".mokata"), exist_ok=True)
            for i in range(n):
                with open(os.path.join(root, f"m_{i}.py"), "w", encoding="utf-8") as fh:
                    fh.write(f"v = {i}\n")
            AF.record_anchors(root, ["m_0.py"])
            return root

        small, large = repo(20), repo(600)
        cs = F.FreshnessController(small, session_id="s")
        cl = F.FreshnessController(large, session_id="s")
        ls, ll = _Layer(_Primary()), _Layer(_Primary())
        cs.ensure_fresh(ls)
        cl.ensure_fresh(ll)

        def warm(c, l):
            t = time.perf_counter()
            c.ensure_fresh(l)
            return time.perf_counter() - t

        warm(cs, ls), warm(cl, ll)
        t_small = min(warm(cs, ls) for _ in range(5))
        t_large = min(warm(cl, ll) for _ in range(5))
        self.assertLess(t_large, 0.05, f"warm path too slow: {t_large*1000:.1f}ms")
        self.assertLess(t_large, t_small * 5 + 0.01,
                        f"anchor pass scales with repo size: "
                        f"{t_small*1000:.2f} vs {t_large*1000:.2f}ms")


if __name__ == "__main__":
    unittest.main()
