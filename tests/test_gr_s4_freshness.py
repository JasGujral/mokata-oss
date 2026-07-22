"""GR.S4 — graph freshness contract (read-time, never a daemon).

The FRESHNESS-BEFORE-ANSWER invariant (re-groom #5): a graph KNOWN stale never answers.
Every query reconciles first — dirty-set (from the PostToolUse async hook) + one cheap
`.git/HEAD` probe + a cold-start index walk — and a stale index rebuilds BEFORE it answers;
a rebuild failure answers from the AST floor on CURRENT files with a loud classed note,
never from stale graph data.

Deliverable → test map (GR.S4 prompt):
  1. dirty-set (async O(1) append) ........ TestDirtySet
  2. query-time reconcile (HEAD/git diff) . TestGitProbes + TestBranchSwitch
  3. FRESHNESS-BEFORE-ANSWER ............... TestStaleNeverAnswers (+ named method) + TestOutOfBand
  3. rebuild-failure → AST-loud ........... TestRebuildFailure
  4. PERF CONTRACT (option B) .............. TestPerfContract
  5. freshness cap → costed note .......... TestCapNote
  9. H-6 fingerprint tripwire (dormant) ... TestH6DormantHook

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest

from mokata.knowledge import freshness as F
from mokata.knowledge.ast_backend import AstBackend
from mokata.knowledge.grep_backend import GrepBackend
from mokata.knowledge.layer import KnowledgeLayer
from mokata.knowledge.query import GraphBackend, QueryResult, Reference


HAVE_GIT = shutil.which("git") is not None


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _git_repo(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")


def _write(root, rel, text):
    ab = os.path.join(root, rel)
    os.makedirs(os.path.dirname(ab) or root, exist_ok=True)
    with open(ab, "w", encoding="utf-8") as fh:
        fh.write(text)
    return ab


def _ast_layer(root, freshness=None):
    """An AST-floor layer (the honest structural floor on a Python repo)."""
    ast = AstBackend(root=root, grep=GrepBackend(root=root))
    return KnowledgeLayer(ast, fallback=None, freshness=freshness)


# A tiny two-symbol module: caller() -> compute() -> helper().
_MOD_V1 = "def helper():\n    return 1\n\n\ndef compute():\n    return helper()\n"
# v2 renames the call target so a STALE answer is detectable: compute now calls other().
_MOD_V2 = "def other():\n    return 2\n\n\ndef compute():\n    return other()\n"


class _FakeGraph(GraphBackend):
    """A stand-in adopted graph whose index refresh can be made to FAIL, and whose stale
    answer is a marked reference we can assert never leaks."""

    is_graph = True

    def __init__(self, root, *, refresh_ok=True):
        self.name = "code-review-graph"
        self.root = root
        self._refresh_ok = refresh_ok
        self.refresh_calls = 0

    def query(self, kind, target, depth=1):
        # The STALE graph answer — a reference the test asserts must NEVER surface after a
        # failed rebuild (a failed rebuild must fall to the AST floor, not serve this).
        return QueryResult(kind=kind, target=target, backend=self.name,
                           references=[Reference(path="STALE_FROM_GRAPH.py", line=1)])

    def refresh_index(self):
        self.refresh_calls += 1
        return self._refresh_ok


# ======================================================================================
# 1 — dirty-set: async O(1) append, atomic, session-scoped, never raises
# ======================================================================================
class TestDirtySet(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".mokata"), exist_ok=True)

    def test_gr_s4_dirty_set_marking(self):
        F.mark_dirty(self.root, ["a.py"], session_id="s1")
        F.mark_dirty(self.root, ["b.py", "c.txt"], session_id="s1")
        got = F.drain_dirty(self.root, session_id="s1")
        self.assertEqual(sorted(got), ["a.py", "b.py", "c.txt"])
        # drained → empty on the next read (consume-once fast path)
        self.assertEqual(F.drain_dirty(self.root, session_id="s1"), [])

    def test_dirty_set_is_session_scoped(self):
        F.mark_dirty(self.root, ["a.py"], session_id="s1")
        F.mark_dirty(self.root, ["z.py"], session_id="s2")
        self.assertEqual(F.drain_dirty(self.root, session_id="s2"), ["z.py"])
        self.assertEqual(F.drain_dirty(self.root, session_id="s1"), ["a.py"])

    def test_mark_dirty_never_raises(self):
        # A bad root must never break the async observability lane.
        try:
            F.mark_dirty(os.path.join(self.root, "nope", "\0bad"), ["x"], session_id="s")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"mark_dirty must never raise: {exc}")

    def test_append_is_additive_not_overwrite(self):
        for i in range(20):
            F.mark_dirty(self.root, [f"f{i}.py"], session_id="s")
        self.assertEqual(len(F.drain_dirty(self.root, session_id="s")), 20)


# ======================================================================================
# 2 — git probes + branch-switch reconcile
# ======================================================================================
@unittest.skipUnless(HAVE_GIT, "git not available")
class TestGitProbes(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        _git_repo(self.root)
        _write(self.root, "m.py", _MOD_V1)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "init")

    def _head(self):
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def test_git_head_sha_matches_rev_parse(self):
        self.assertEqual(F.git_head_sha(self.root), self._head())

    def test_git_head_sha_none_without_git(self):
        plain = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        self.assertIsNone(F.git_head_sha(plain))

    def test_git_changed_since_lists_only_changed(self):
        base = self._head()
        _write(self.root, "m.py", _MOD_V2)
        _write(self.root, "new.py", "x = 1\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "edit")
        changed = F.git_changed_since(self.root, base)
        self.assertIn("m.py", changed)
        self.assertIn("new.py", changed)


@unittest.skipUnless(HAVE_GIT, "git not available")
class TestBranchSwitch(unittest.TestCase):
    def test_gr_s4_branch_switch_reconcile(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        _git_repo(root)
        _write(root, "m.py", _MOD_V1)
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "init")

        ctrl = F.FreshnessController(root, session_id="s")
        layer = _ast_layer(root, freshness=ctrl)
        # prime: cold-start baseline + record the current HEAD as last-indexed
        ctrl.ensure_fresh(layer)

        # HEAD moves out-of-band (a branch switch / new commit), no hook fired.
        _git(root, "checkout", "-q", "-b", "feature")
        _write(root, "m.py", _MOD_V2)
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "feature edit")

        out = ctrl.ensure_fresh(layer)
        self.assertIn("m.py", out.changed)   # batched diff vs the persisted last-indexed SHA
        self.assertTrue(out.rebuilt)


# ======================================================================================
# 3 — FRESHNESS-BEFORE-ANSWER: a stale graph never answers
# ======================================================================================
class TestStaleNeverAnswers(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".mokata"), exist_ok=True)
        _write(self.root, "m.py", _MOD_V1)

    def test_stale_graph_never_answers(self):
        """Named for re-groom #5: edit → immediate query answers from FRESH state.

        The AST floor caches its edge index in-memory; without the freshness contract a reused
        layer serves the STALE index. With it, a dirty-set signal invalidates the index BEFORE
        the answer, so `callers('helper')` reflects the edit (helper no longer called)."""
        ctrl = F.FreshnessController(self.root, session_id="s")
        layer = _ast_layer(self.root, freshness=ctrl)

        r1 = layer.callers("helper")
        self.assertTrue(any("m.py" in ref.path for ref in r1.references),
                        "baseline: compute() calls helper()")

        # Edit so helper is no longer called (compute now calls other()), and mark it dirty
        # exactly as the PostToolUse hook would.
        _write(self.root, "m.py", _MOD_V2)
        F.mark_dirty(self.root, ["m.py"], session_id="s")

        r2 = layer.callers("helper")
        # FRESH: helper has zero AST callers now → NOT a clean AST edge. STALE code (the old,
        # freshness-free layer) would still return the cached "compute calls helper" AST edge
        # (degraded=False, non-empty) — so this fails on old code.
        self.assertTrue(r2.degraded or not r2.references,
                        "a stale 'helper is called' answer leaked after the edit")
        # And the fresh state IS reflected: querying the NEW target resolves.
        r3 = layer.callers("other")
        self.assertTrue(any("m.py" in ref.path for ref in r3.references),
                        "fresh: compute() now calls other()")


class TestOutOfBand(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".mokata"), exist_ok=True)
        _write(self.root, "m.py", _MOD_V1)

    def test_gr_s4_out_of_band_edit_caught_at_query(self):
        """An editor edit, NO hook fired, HEAD unchanged (non-git repo) — the mtime/hash
        recheck on referenced files catches it at query time and rebuilds before serving."""
        ctrl = F.FreshnessController(self.root, session_id="s")
        layer = _ast_layer(self.root, freshness=ctrl)

        r1 = layer.callers("helper")
        self.assertTrue(any("m.py" in ref.path for ref in r1.references))

        # Edit WITHOUT marking dirty (no hook) and WITHOUT git — the file's content changed.
        time.sleep(0.01)
        _write(self.root, "m.py", _MOD_V2)

        r2 = layer.callers("other")
        # FRESH via the post-answer staleness recheck: a clean AST edge (compute calls other).
        # STALE code answers from the cached v1 index → no 'other' AST edge → grep fallback
        # (degraded=True), so this fails on old code.
        self.assertFalse(r2.degraded,
                         "out-of-band edit not caught: served the stale (grep-degraded) answer")
        self.assertTrue(any("m.py" in ref.path for ref in r2.references))


# ======================================================================================
# 3 — rebuild failure → AST floor on current files + loud classed note
# ======================================================================================
class TestRebuildFailure(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".mokata"), exist_ok=True)
        _write(self.root, "m.py", _MOD_V2)  # AST floor answers `other`

    def test_gr_s4_rebuild_failure_ast_loud(self):
        graph = _FakeGraph(self.root, refresh_ok=False)   # its index rebuild FAILS
        ast_floor = AstBackend(root=self.root, grep=GrepBackend(root=self.root))
        ctrl = F.FreshnessController(self.root, session_id="s")
        layer = KnowledgeLayer(graph, fallback=ast_floor, freshness=ctrl)

        ctrl.ensure_fresh(layer)             # prime
        F.mark_dirty(self.root, ["m.py"], session_id="s")   # graph is now KNOWN stale

        res = layer.callers("other")
        # NEVER the stale graph answer.
        self.assertFalse(any("STALE_FROM_GRAPH" in r.path for r in res.references),
                         "a failed rebuild served STALE graph data")
        # Answered from the AST floor on CURRENT files, loudly.
        self.assertTrue(res.degraded)
        self.assertIn("AST floor", res.note)
        self.assertTrue(any("m.py" in r.path for r in res.references))
        self.assertGreaterEqual(graph.refresh_calls, 1)   # a rebuild WAS attempted


# ======================================================================================
# 4 — PERF CONTRACT (option B): cost tracks churn, not repo size
# ======================================================================================
class TestPerfContract(unittest.TestCase):
    def _big_repo(self, n):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.makedirs(os.path.join(root, ".mokata"), exist_ok=True)
        for i in range(n):
            _write(root, f"pkg/mod_{i}.py", f"def f_{i}():\n    return {i}\n")
        return root

    def test_gr_s4_perf_contract(self):
        small = self._big_repo(20)
        large = self._big_repo(600)
        cs = F.FreshnessController(small, session_id="s")
        cl = F.FreshnessController(large, session_id="s")
        ls = _ast_layer(small, freshness=cs)
        ll = _ast_layer(large, freshness=cl)
        # Prime cold start on both (the ONE mtime walk per session).
        cs.ensure_fresh(ls)
        cl.ensure_fresh(ll)

        def warm(ctrl, layer):
            t = time.perf_counter()
            ctrl.ensure_fresh(layer)
            return time.perf_counter() - t

        # discard first (filesystem cache warmup), then measure
        warm(cs, ls); warm(cl, ll)
        t_small = min(warm(cs, ls) for _ in range(5))
        t_large = min(warm(cl, ll) for _ in range(5))

        # Warm-path overhead is bounded (dirty-set read + one HEAD stat + one state read).
        # Generous CI margin; the SHAPE is the real assertion.
        self.assertLess(t_large, 0.05, f"warm path too slow: {t_large*1000:.1f}ms")
        # SHAPE: a 30x larger repo does NOT cost 30x the warm-path time (cost ∝ churn, not size).
        self.assertLess(t_large, t_small * 5 + 0.01,
                        f"warm cost scales with repo size: {t_small*1000:.2f} vs {t_large*1000:.2f}ms")


# ======================================================================================
# 5 — freshness cap: honest costed note, never a block
# ======================================================================================
class TestCapNote(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".mokata"), exist_ok=True)
        _write(self.root, "m.py", _MOD_V1)

    def test_gr_s4_cap_note_honesty(self):
        ctrl = F.FreshnessController(self.root, session_id="s")
        layer = _ast_layer(self.root, freshness=ctrl)
        ctrl.ensure_fresh(layer)  # prime

        # A dirty-set past the cap must produce a costed NOTE, never raise / block.
        many = [f"f{i}.py" for i in range(F.FRESHNESS_CHANGE_CAP + 25)]
        F.mark_dirty(self.root, many, session_id="s")
        out = ctrl.ensure_fresh(layer)
        self.assertTrue(out.capped)
        self.assertTrue(out.note)
        self.assertIn("cap", out.note.lower())
        # The query still ANSWERS (never blocked).
        res = layer.callers("helper")
        self.assertIsNotNone(res)


# ======================================================================================
# no-controller path is byte-identical (regression guard)
# ======================================================================================
class TestNoFreshnessByteIdentical(unittest.TestCase):
    def test_layer_without_freshness_unchanged(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        _write(root, "m.py", _MOD_V1)
        plain = _ast_layer(root, freshness=None)
        r = plain.callers("helper")
        self.assertTrue(any("m.py" in ref.path for ref in r.references))
        self.assertFalse(r.degraded)


# ======================================================================================
# GR.S2(k) seam — the adopted graph's proactive index refresh
# ======================================================================================
class TestGraphBackendRefreshSeam(unittest.TestCase):
    def test_refresh_index_calls_client_refresh_incremental(self):
        from mokata.knowledge.graph_backend import CodeReviewGraphBackend

        class _Client:
            def __init__(self):
                self.calls = []

            def refresh(self, root, full=False):
                self.calls.append((root, full))
                return True

        c = _Client()
        b = CodeReviewGraphBackend(name="code-review-graph", root="/x", client=c)
        self.assertTrue(b.refresh_index())
        self.assertEqual(c.calls, [("/x", False)])   # incremental (never a full rebuild here)

    def test_refresh_index_degrades_clean_on_error(self):
        from mokata.knowledge.graph_backend import CodeReviewGraphBackend

        class _Bad:
            def refresh(self, root, full=False):
                raise RuntimeError("down")

        b = CodeReviewGraphBackend(name="code-review-graph", root="/x", client=_Bad())
        self.assertFalse(b.refresh_index())          # never raises → treated as rebuild-failed


# ======================================================================================
# 9 — H-6 fingerprint tripwire: NAMED HOOK, dormant until 0.0.16
# ======================================================================================
class TestH6DormantHook(unittest.TestCase):
    def test_fingerprint_tripwire_is_dormant(self):
        # The hook EXISTS (named wire point) but stays dormant: with no fingerprint supplied
        # it is a no-op (returns False — "no forced refresh"), exactly the AP-SD hook precedent.
        self.assertFalse(F.fingerprint_forces_refresh(None, None))
        self.assertFalse(F.fingerprint_forces_refresh("abc", "abc"))

    def test_fingerprint_mismatch_shape_ready_for_0_0_16(self):
        # When 0.0.16 wakes it, a mismatch is the same forced-refresh signal. The SHAPE is
        # asserted now so the wake is a one-line flip, not a redesign.
        self.assertTrue(F.fingerprint_forces_refresh("abc", "def"))


if __name__ == "__main__":
    unittest.main()
