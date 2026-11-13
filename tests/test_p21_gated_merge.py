"""0.0.27 P21 `ID-MERGE-GATED` — the gated merge, as a user meets it.

A session worktree's branch lands in the main checkout's branch ONLY when (a) mokata's own runner
ran the FULL suite at the branch's head on a clean tree and it passed, every acceptance criterion
has a passing test, and the review passed at that head; and (b) a human approved exactly that
merge in their own terminal. The branch moves by compare-and-swap (the commit point); only then is
the main checkout brought to the merge, under git's own index lock carrying mokata's owner record.
A failure after the commit point keeps that lock until an explicit Recover (R1). Recover removes
only mokata's own lock, only when its owner is provably gone, only on approval (R2).

On REAL git, Claude Code-shaped worktrees (`test_p31_session_worktrees`' fixture). Faults are
injected through `merge_gate.Hooks` — a seam the caller passes, never an environment switch.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from _plan import sealed
from test_p31_session_worktrees import _git, _Repo, _write
from mokata import approval
from mokata import merge_gate as MG
from mokata import merge_lock as ML
from mokata import merge_spec as MSL
from mokata import merge_suite as MS
from mokata import run_intent
from mokata import session_worktrees as SW
from mokata.git_safe import Git, GitSafeError, resolve_git, scrubbed_env

TID = "tests/test_f.py::test_f"


def _read(root, rel):
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _state(root):
    from mokata.state import StateStore
    from mokata.tdd_state import state_dir
    return StateStore(state_dir(root))


class _Merge(_Repo):
    """A main checkout + one session worktree with evidence that passes (unless a test breaks it)."""

    def setUp(self):
        super().setUp()
        _write(self.main, "check.py", "import sys\nsys.exit(0)\n")
        _write(self.main, "tests/test_f.py", "def test_f():\n    pass\n")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "checker")
        mf = os.path.join(self.main, ".mokata", "manifest.json")
        with open(mf, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("settings", {}).setdefault("baseline", {})["test_command"] = \
            f"{sys.executable} check.py"
        with open(mf, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def session(self, name="a", sid="S1", run="R1", files=None):
        """A worktree bound to `sid`, driving run `run`, with one commit of `files`."""
        wt = self.worktree(name)
        for rel, text in (files or {f"src/{name}.py": f"{name.upper()} = 1\n"}).items():
            _write(wt, rel, text)
        _git(wt, "add", "-A")
        _git(wt, "commit", "-qm", f"feature {name}")
        SW.bind(self.main, sid, wt)
        run_intent.bind_registered(self.main, sid, run)
        _state(self.main).write("emitted_spec__" + run, sealed(
            {"title": f"Feature {name}", "criteria": [{"id": "AC1", "text": "it works"}]}))
        from mokata.test_runs import record_test_run
        record_test_run(self.main, run, passed=False, ac_map={"AC1": [TID]}, source="cli")
        return wt

    def evidence(self, wt, run="R1"):
        """The suite through mokata's runner, the spec legs at this head (0.0.28 F), then a passing
        review stamped at this head."""
        res = MS.run_full_suite(self.main, wt)
        self.assertTrue(res["passed"], res)
        MSL.run_spec_leg(self.main, wt)
        self.review(wt, run)

    def review(self, wt, run="R1", passed=True):
        from mokata.config import Surface
        from mokata.progress_events import record_review_verdict
        record_review_verdict(Surface.load(self.main), passed=passed, independent=True,
                              run_id=run, tree_root=wt)

    def approved(self, wt, strategy="merge-commit"):
        plan = MG.plan_merge(self.main, wt, strategy)
        self.assertTrue(plan.ok, plan.render())
        pid = MG.propose(self.main, plan)["proposal_id"]
        approval.approve(self.main, pid)
        return pid, plan

    def head(self, tree="", ref="HEAD"):
        return _git(tree or self.main, "rev-parse", ref).strip()

    def lock(self):
        return os.path.join(self.main, ".git", "index.lock")


# ======================================================================= AC1 + AC8: the approval
class TheHumanApproves(_Merge):

    def test_merge_needs_a_human_minted_approval(self):
        """Nothing merges on a proposal alone, on an in-chat (relayed) approval, or from MCP's
        approve/confirm flags — only an approval a human typed in their own terminal."""
        wt = self.session()
        self.evidence(wt)
        base = self.head()
        plan = MG.plan_merge(self.main, wt)
        pid = MG.propose(self.main, plan)["proposal_id"]
        out = MG.run_merge(self.main, pid)
        self.assertEqual(MG.REFUSED, out.state)
        self.assertEqual(base, self.head())
        approval.approve(self.main, pid, actor=MG.CHAT_ACTOR)
        ok, code, _why = MG.ready_to_run(self.main, pid)
        self.assertEqual((False, "chat-relayed"), (ok, code))
        self.assertEqual(MG.REFUSED, MG.run_merge(self.main, pid).state)
        from mokata.mcp.tools_worktree import worktree_merge
        # 0.0.28 I2: the `approve`/`confirm` flags no longer exist, so "an MCP flag merged" is
        # structurally impossible: a direct call raises TypeError (over MCP: refused invalid_input),
        # and a plain call only ever proposes.
        with self.assertRaises(TypeError):
            worktree_merge(path=self.main, target=wt, approve=True, confirm=True)
        self.assertEqual(base, self.head(), "an MCP flag merged")
        r = worktree_merge(path=self.main, target=wt)
        self.assertEqual("proposed", r["status"], r)
        self.assertFalse(r["committed"])
        self.assertEqual(base, self.head(), "an MCP flag merged")

    def test_an_approval_licenses_exactly_one_merge(self):
        """The approval is burned at the commit point: re-running it never merges twice."""
        wt = self.session()
        self.evidence(wt)
        pid, _plan = self.approved(wt)
        self.assertEqual(MG.MERGED, MG.run_merge(self.main, pid).state)
        tip = self.head()
        self.assertNotEqual(MG.MERGED, MG.run_merge(self.main, pid).state)
        self.assertEqual(tip, self.head())


# ======================================================================= AC2–AC4: the evidence
class TheEvidenceIsAtTheHead(_Merge):

    def codes(self, wt):
        return {b["code"] for b in MG.plan_merge(self.main, wt).blocked}

    def test_each_missing_piece_of_evidence_blocks_the_merge_by_name(self):
        """No suite, no review, a failed review, a behind branch, a dirty worktree: each names
        itself, and the plan proposes nothing."""
        wt = self.session()
        self.assertIn("suite-not-runner", self.codes(wt))       # only the agent's attested run
        self.assertIn("ac-unproven", self.codes(wt))
        MS.run_full_suite(self.main, wt)
        self.assertIn("review-blocks", self.codes(wt))
        self.review(wt, passed=False)
        self.assertIn("review-blocks", self.codes(wt))
        self.review(wt)
        self.assertEqual({"spec-leg-not-run"}, self.codes(wt))   # 0.0.28 F: the spec legs too
        MSL.run_spec_leg(self.main, wt)
        self.assertEqual(set(), self.codes(wt))
        _write(wt, "scratch.txt", "x\n")                        # untracked counts
        self.assertIn("dirty-worktree", self.codes(wt))
        os.remove(os.path.join(wt, "scratch.txt"))
        self.commit(self.main, "src/other.py", "O = 1\n", "landed meanwhile")
        self.assertIn("behind", self.codes(wt))

    def test_a_worktree_with_no_run_blocks_as_no_run(self):
        """P1: no mokata run bound means no evidence — `no-run`, naming the override command."""
        wt = self.worktree("b")
        self.commit(wt, "src/b.py", "B = 1\n", "b")
        plan = MG.plan_merge(self.main, wt)
        self.assertEqual(["no-run"], [b["code"] for b in plan.blocked])
        self.assertIn("merge-override", plan.reason())

    def test_a_suite_run_during_which_head_moved_is_not_evidence(self):
        """A run at another commit, or one during which the branch moved, proves nothing here."""
        wt = self.session()
        self.evidence(wt)
        self.commit(wt, "src/late.py", "L = 1\n", "after the run")
        self.assertIn("suite-stale", self.codes(wt))
        from mokata.test_runs import key as tkey
        st = _state(self.main)
        data = st.read(tkey("R1"))
        data["last_suite"]["tree"]["head_before"] = "0" * 40
        data["last_suite"]["tree"]["head_after"] = self.head(wt)
        st.write(tkey("R1"), data)
        self.assertIn("suite-moved", self.codes(wt))

    def test_evidence_from_another_worktree_never_satisfies_this_merge(self):
        wt = self.session()
        self.evidence(wt)
        from mokata.test_runs import key as tkey
        st = _state(self.main)
        data = st.read(tkey("R1"))
        data["last_suite"]["tree"]["worktree"] = self.main
        st.write(tkey("R1"), data)
        self.assertIn("suite-elsewhere", self.codes(wt))

    def test_a_subset_or_attested_run_is_not_the_suite(self):
        """`mokata progress test-run` (attested) and an IDE re-run of some tests are shown, not
        counted: only the runner's full, unfiltered command is the suite."""
        wt = self.session()
        self.review(wt)
        from mokata.test_runs import record_test_run
        record_test_run(wt, "R1", passed=True, command="pytest", tests=[TID], source="cli")
        self.assertIn("suite-not-runner", self.codes(wt))
        record_test_run(wt, "R1", passed=True, command="pytest", tests=[TID], source="runner",
                        tree={"scope": "subset"})
        self.assertIn("suite-subset", self.codes(wt))

    def test_unreadable_manifest_blocks_never_passes(self):
        """A damaged record reads UNKNOWN — it never collapses to absent, and never passes."""
        wt = self.session()
        self.evidence(wt)
        from mokata.test_runs import key as tkey
        path = _state(self.main).path(tkey("R1"))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertIn("unknown-evidence", self.codes(wt))

    def test_unstamped_verdict_reads_unknown(self):
        """A review verdict that does not say which commit it reviewed is not evidence for one."""
        wt = self.session()
        MS.run_full_suite(self.main, wt)
        from mokata.config import Surface
        from mokata.progress_events import record_review_verdict
        record_review_verdict(Surface.load(self.main), passed=True, independent=True, run_id="R1")
        self.assertIn("review-unstamped", self.codes(wt))
        self.review(wt)
        self.commit(wt, "src/late.py", "L = 1\n", "after review")
        MS.run_full_suite(self.main, wt)
        self.assertIn("review-stale", self.codes(wt))


# ======================================================================= AC5: what lands
class WhatLandsIsWhatWasTested(_Merge):

    def test_what_lands_is_byte_identical_to_what_was_tested(self):
        """For every strategy the main checkout's new tip has exactly the tree that was tested."""
        for strategy in MG.STRATEGIES:
            with self.subTest(strategy):
                wt = self.session(name=f"s{strategy[0]}", sid=f"S-{strategy}", run=f"R-{strategy}")
                self.evidence(wt, run=f"R-{strategy}")
                tested = self.head(wt, "HEAD^{tree}")
                pid, _p = self.approved(wt, strategy)
                out = MG.run_merge(self.main, pid)
                self.assertEqual(MG.MERGED, out.state, out.message)
                self.assertEqual(tested, self.head(ref="HEAD^{tree}"))
                self.assertEqual("", _git(self.main, "status", "--porcelain",
                                          "--untracked-files=no").strip())
                if strategy == "ff":
                    self.assertEqual(self.head(wt), self.head())
                parents = _git(self.main, "rev-list", "--parents", "-n", "1", "HEAD").split()
                self.assertEqual({"merge-commit": 3, "squash": 2, "ff": 2}[strategy], len(parents))
                self.assertTrue(os.path.isdir(wt), "the merge deleted the worktree")

    def test_a_conflict_is_its_own_state_and_touches_nothing(self):
        wt = self.session(files={"README.md": "mine\n"})
        self.evidence(wt)
        pid, _p = self.approved(wt)
        with mock.patch.object(MG, "_SHA", MG._SHA):
            # a merge-tree that disagrees with the tested tree is refused by name
            real = MG.Git.run

            def lying(self_, cwd, *args, **kw):
                if args and args[0] == "merge-tree":
                    return 1, b"0" * 40 + b"\nCONFLICT (content): README.md\n", ""
                return real(self_, cwd, *args, **kw)
            with mock.patch.object(MG.Git, "run", lying):
                out = MG.run_merge(self.main, pid)
        self.assertEqual(MG.CONFLICT, out.state)
        self.assertEqual("hello\n", _read(self.main, "README.md"))


# ======================================================================= AC6–AC7: races + faults
class NothingChangesUnlessItAllLands(_Merge):

    def test_branch_moved_after_approval_refuses_and_changes_nothing(self):
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)
        base = self.head()
        self.commit(wt, "src/late.py", "L = 1\n", "after approval")
        out = MG.run_merge(self.main, pid)
        self.assertEqual(MG.HEAD_MOVED, out.state, out.message)
        self.assertEqual(base, self.head())
        self.assertFalse(os.path.exists(self.lock()))

    def test_a_human_commit_before_the_merge_wins_and_nothing_is_touched(self):
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)
        mine = self.commit(self.main, "src/main.py", "M = 1\n", "the human's own commit")
        out = MG.run_merge(self.main, pid)
        self.assertEqual(MG.BASE_MOVED, out.state, out.message)
        self.assertEqual(mine, self.head())
        self.assertFalse(os.path.exists(os.path.join(self.main, "src", "a.py")))

    def test_a_racer_moving_the_base_loses_to_the_cas(self):
        """A plumbing `update-ref` between the re-measure and the CAS: the CAS refuses."""
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)
        racer = _git(self.main, "commit-tree", "-m", "racer", "-p", "HEAD", "HEAD^{tree}").strip()

        def at(step):
            if step == "s5":
                _git(self.main, "update-ref", "refs/heads/main", racer)
        out = MG.run_merge(self.main, pid, hooks=MG.Hooks(at=at))
        self.assertEqual(MG.BASE_MOVED, out.state, out.message)
        self.assertEqual(racer, self.head())
        self.assertFalse(os.path.exists(os.path.join(self.main, "src", "a.py")))
        self.assertFalse(os.path.exists(self.lock()), "the lock outlived a refused merge")

    def test_git_in_the_main_checkout_waits_while_the_merge_writes(self):
        """While the merge writes the main checkout, a `git commit` there fails on OUR lock — it
        cannot record the base's index on top of the merge."""
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)
        seen = {}

        def at(step):
            if step == "s6-read-tree":
                seen["commit"] = subprocess.run(
                    ["git", "-C", self.main, "commit", "--allow-empty", "-qm", "sneak"],
                    capture_output=True, text=True)
                seen["record"] = ML.read_record(self.lock())[0]
        out = MG.run_merge(self.main, pid, hooks=MG.Hooks(at=at))
        self.assertEqual(MG.MERGED, out.state, out.message)
        self.assertNotEqual(0, seen["commit"].returncode)
        self.assertIn("index.lock", seen["commit"].stderr)
        self.assertEqual(pid, seen["record"]["merge"], "the lock did not carry our owner record")

    def test_a_checkout_failing_after_the_commit_point_is_merged_incomplete_and_recoverable(self):
        """R1: an s6 failure KEEPS `.git/index.lock` and its owner record until an explicit
        Recover; meanwhile `git commit` in the main checkout cannot undo the merge. Recover needs
        its own approval to remove the lock, then finishes the checkout from git truth."""
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)

        def at(step):
            if step == "s6-commit":
                raise OSError("disk full (injected)")
        out = MG.run_merge(self.main, pid, hooks=MG.Hooks(at=at))
        self.assertEqual(MG.INCOMPLETE, out.state, out.message)
        merged = out.facts["merge"]
        self.assertEqual(merged, self.head())
        self.assertTrue(os.path.exists(self.lock()), "R1: the lock was dropped after the commit point")
        self.assertEqual(pid, ML.read_record(self.lock())[0]["merge"], "R1: the marker is gone")
        sneak = subprocess.run(["git", "-C", self.main, "commit", "--allow-empty", "-qm", "x"],
                               capture_output=True, text=True)
        self.assertNotEqual(0, sneak.returncode)
        self.assertIn(pid, [j["merge_id"] for j in MG.journals(self.main)])
        first = MG.recover(self.main, pid)
        self.assertEqual(MG.LOCK_HELD, first.state, first.message)
        unlock = first.facts["proposal_id"]
        self.assertTrue(os.path.exists(self.lock()), "a lock was removed without approval")
        approval.approve(self.main, unlock)
        done = MG.recover(self.main, pid, unlock_proposal=unlock)
        self.assertEqual(MG.MERGED, done.state, done.message)
        self.assertFalse(os.path.exists(self.lock()))
        self.assertEqual("", _git(self.main, "status", "--porcelain",
                                  "--untracked-files=no").strip())
        self.assertEqual("A = 1\n", _read(self.main, "src/a.py"))
        self.assertNotIn(pid, [j["merge_id"] for j in MG.journals(self.main)])

    def test_head_switched_after_the_commit_point_is_never_reported_merged(self):
        """The CAS binds the BRANCH, not HEAD. If the main checkout's HEAD is switched to another
        branch between the re-measure and the checkout, the merge has landed on its branch but NO
        file is written onto the other branch's checkout: the outcome is merged-checkout-incomplete
        with the index lock KEPT (R1) and an honest message — never MERGED. Switching back and
        Recovering (approved) finishes it."""
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)
        _git(self.main, "branch", "other")
        other = self.head(ref="other")

        def at(step):
            if step == "s6":
                _git(self.main, "symbolic-ref", "HEAD", "refs/heads/other")
        out = MG.run_merge(self.main, pid, hooks=MG.Hooks(at=at))
        self.assertEqual(MG.INCOMPLETE, out.state, out.message)
        self.assertIn("HEAD moved", out.message)
        self.assertEqual(out.facts["merge"], self.head(ref="refs/heads/main"))
        self.assertEqual(other, self.head(ref="other"), "the other branch was moved")
        self.assertFalse(os.path.exists(os.path.join(self.main, "src", "a.py")),
                         "M's files were written onto another branch's checkout")
        self.assertTrue(os.path.exists(self.lock()), "R1: the lock was dropped")
        self.assertEqual(pid, ML.read_record(self.lock())[0]["merge"])
        unlock = MG.recover(self.main, pid).facts["proposal_id"]
        approval.approve(self.main, unlock)
        still = MG.recover(self.main, pid, unlock_proposal=unlock)
        self.assertNotEqual(MG.MERGED, still.state, "Recover finished onto the wrong branch")
        self.assertFalse(os.path.exists(os.path.join(self.main, "src", "a.py")))
        _git(self.main, "symbolic-ref", "HEAD", "refs/heads/main")
        if os.path.exists(self.lock()):
            unlock = MG.recover(self.main, pid).facts["proposal_id"]
            approval.approve(self.main, unlock)
            done = MG.recover(self.main, pid, unlock_proposal=unlock)
        else:
            done = MG.recover(self.main, pid)
        self.assertEqual(MG.MERGED, done.state, done.message)
        self.assertEqual("A = 1\n", _read(self.main, "src/a.py"))

    def test_branch_moved_off_the_merge_after_the_commit_point_is_never_reported_merged(self):
        """HEAD still on the merged branch, but the BRANCH moves off M between the CAS and the
        checkout (a concurrent commit or reset): no file is written, the index lock is KEPT (R1),
        the outcome is merged-checkout-incomplete with an honest message — never MERGED."""
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)
        base = self.head()
        racer = _git(self.main, "commit-tree", "-m", "racer", "-p", base, base + "^{tree}").strip()

        def at(step):
            if step == "s6":
                _git(self.main, "update-ref", "refs/heads/main", racer)
        out = MG.run_merge(self.main, pid, hooks=MG.Hooks(at=at))
        self.assertEqual(MG.INCOMPLETE, out.state, out.message)
        self.assertIn("not the merge", out.message)
        self.assertIn("no file was written", out.message)
        self.assertEqual("refs/heads/main", _git(self.main, "symbolic-ref", "HEAD").strip())
        self.assertEqual(racer, self.head())
        self.assertFalse(os.path.exists(os.path.join(self.main, "src", "a.py")),
                         "M's files were written onto a branch that is no longer M")
        self.assertTrue(os.path.exists(self.lock()), "R1: the lock was dropped")
        self.assertEqual(pid, ML.read_record(self.lock())[0]["merge"])

    def test_a_file_edited_during_the_merge_is_never_overwritten(self):
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)

        def at(step):
            if step == "s6-read-tree":
                _write(self.main, "src/a.py", "HAND = 'edited'\n")
        out = MG.run_merge(self.main, pid, hooks=MG.Hooks(at=at))
        self.assertEqual(MG.INCOMPLETE, out.state, out.message)
        self.assertEqual("HAND = 'edited'\n", _read(self.main, "src/a.py"))
        unlock = MG.recover(self.main, pid).facts["proposal_id"]
        approval.approve(self.main, unlock)
        done = MG.recover(self.main, pid, unlock_proposal=unlock)
        self.assertEqual(MG.MERGED, done.state, done.message)
        self.assertEqual("HAND = 'edited'\n", _read(self.main, "src/a.py"), "an edit was overwritten")
        self.assertIn("src/a.py", done.facts["kept"])

    def test_a_busy_checkout_is_retryable_not_failed(self):
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)
        with open(self.lock(), "wb") as fh:
            fh.write(b"someone else's git\n")
        out = MG.run_merge(self.main, pid)
        self.assertEqual(MG.BUSY, out.state, out.message)
        self.assertIn(MG.INDEX_LOCKED, {b["code"] for b in out.facts["blocked"]})
        self.assertEqual(b"someone else's git\n", open(self.lock(), "rb").read(),
                         "a foreign lock was touched")
        self.assertTrue(MG.ready_to_run(self.main, pid)[0], "busy burned the approval")
        os.remove(self.lock())
        self.assertEqual(MG.MERGED, MG.run_merge(self.main, pid).state)

    def test_a_dirty_or_moved_checkout_refuses_before_the_commit_point(self):
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)
        base = self.head()

        def at(step):
            if step == "s4":
                _write(self.main, "README.md", "edited after approval\n")
        out = MG.run_merge(self.main, pid, hooks=MG.Hooks(at=at))
        self.assertEqual(MG.DIRTY, out.state, out.message)
        self.assertEqual(base, self.head())
        self.assertFalse(os.path.exists(self.lock()))
        self.assertEqual("edited after approval\n", _read(self.main, "README.md"))


# ======================================================================= R1: the lock mechanics
class TheIndexLock(unittest.TestCase):

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.gd = self._t.name

    def tearDown(self):
        self._t.cleanup()

    def test_s4_reads_are_lock_free(self):
        """R1: every re-measure under our lock is a READ with `GIT_OPTIONAL_LOCKS=0` (no refresh
        that would contend for the index lock), and no GIT_* variable from the caller leaks in."""
        env = scrubbed_env({"GIT_DIR": "/elsewhere", "GIT_INDEX_FILE": "/x", "PATH": "/bin"},
                           {"GIT_OPTIONAL_LOCKS": "0"})
        self.assertEqual({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
                         {k: v for k, v in env.items() if k.startswith("GIT_")})
        with tempfile.TemporaryDirectory() as d:
            calls = []
            g = Git(d)
            real = g._exec

            def spy(cwd, args, **kw):
                calls.append((list(args), dict(kw.get("env") or {})))
                return real(cwd, args, **kw)
            with g, mock.patch.object(g, "_exec", spy):
                MG._remeasure(g, d, {"into": "refs/heads/main", "base": "0" * 40,
                                     "branch": "refs/heads/x", "head": "1" * 40}, "2" * 40)
            for args, env in calls:
                with self.subTest(args[:2]):
                    self.assertEqual("0", env.get("GIT_OPTIONAL_LOCKS"), args)
                    self.assertNotIn(args[0], ("write-tree", "update-index", "add"))

    def test_windows_closes_the_handle_before_the_rename(self):
        """R1: Windows cannot rename an open file — the lock's handle is closed BEFORE os.replace."""
        lock = ML.IndexLock(self.gd, "m1").acquire()
        tmp = os.path.join(self.gd, "tmp.index")
        with open(tmp, "wb") as fh:
            fh.write(ML.INDEX_SIGNATURE + b"\0" * 40)
        seen = {}
        real = os.replace

        def spy(a, b):
            seen["fd_at_rename"] = lock.fd
            return real(a, b)
        with mock.patch.object(ML.os, "replace", spy):
            lock.commit_index(tmp)
        self.assertIsNone(seen["fd_at_rename"])
        self.assertTrue(open(os.path.join(self.gd, "index"), "rb").read().startswith(b"DIRC"))
        self.assertFalse(os.path.exists(ML.lock_path(self.gd)))

    def test_a_failed_commit_leaves_the_lock_held_and_still_marked_ours(self):
        """R1: when the rename onto `.git/index` fails AFTER the new index bytes were written into
        the lock, the lock is not left holding anonymous index bytes — our owner record is written
        back, so Recover can still prove the lock is mokata's and offer its approved removal."""
        with open(os.path.join(self.gd, "index"), "wb") as fh:
            fh.write(ML.INDEX_SIGNATURE + b"original" + b"\0" * 32)
        lock = ML.IndexLock(self.gd, "m4").acquire()
        tmp = os.path.join(self.gd, "new.index")
        with open(tmp, "wb") as fh:
            fh.write(ML.INDEX_SIGNATURE + b"merged" + b"\0" * 40)
        with mock.patch.object(ML.os, "replace", side_effect=OSError("rename refused (injected)")):
            with self.assertRaises(OSError):
                lock.commit_index(tmp)
        with open(ML.lock_path(self.gd), "rb") as fh:
            self.assertTrue(fh.read().startswith(ML.MAGIC), "the lock lost its owner record")
        self.assertEqual("m4", ML.read_record(ML.lock_path(self.gd))[0]["merge"])
        with open(os.path.join(self.gd, "index"), "rb") as fh:
            self.assertIn(b"original", fh.read())

    def test_the_record_and_an_index_can_never_share_a_prefix(self):
        """R2 proof: every byte string mokata writes into index.lock starts with MAGIC, and MAGIC
        is not — and cannot be extended into — a git index header (`DIRC`), so the signature check
        alone refuses our record even if the explicit MAGIC check were removed."""
        self.assertFalse(ML.MAGIC.startswith(ML.INDEX_SIGNATURE))
        self.assertFalse(ML.INDEX_SIGNATURE.startswith(ML.MAGIC[:len(ML.INDEX_SIGNATURE)]))
        lock = ML.IndexLock(self.gd, "m3").acquire()
        try:
            self.assertTrue(open(ML.lock_path(self.gd), "rb").read().startswith(ML.MAGIC))
        finally:
            lock.release()

    def test_our_owner_record_can_never_become_the_index(self):
        """R2: the record lives INSIDE index.lock, so the one rename onto `.git/index` is guarded —
        bytes that start with our MAGIC, or are not a git index, are refused, the index is left
        as it was and the lock still carries our record."""
        with open(os.path.join(self.gd, "index"), "wb") as fh:
            fh.write(ML.INDEX_SIGNATURE + b"original" + b"\0" * 32)
        lock = ML.IndexLock(self.gd, "m2").acquire()
        for bad in (open(ML.lock_path(self.gd), "rb").read(), b"not an index at all",
                    ML.MAGIC + ML.INDEX_SIGNATURE + b"\0" * 40):
            with self.subTest(bad[:12]):
                tmp = os.path.join(self.gd, "bad.index")
                with open(tmp, "wb") as fh:
                    fh.write(bad)
                if lock.fd is None:
                    lock = ML.IndexLock(self.gd, "m2")
                    os.remove(ML.lock_path(self.gd))
                    lock.acquire()
                with self.assertRaises(ML.LockError):
                    lock.commit_index(tmp)
                self.assertIn(b"original", open(os.path.join(self.gd, "index"), "rb").read())
                self.assertEqual("m2", ML.read_record(ML.lock_path(self.gd))[0]["merge"])
        lock.release()      # the refused commit left the lock held; Windows cannot delete an open file


class StaleLocksAreProvenNotGuessed(unittest.TestCase):
    """R2: owner identity is host + boot id + pid + pid start time, re-verified at removal."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.gd = self._t.name
        self.path = ML.lock_path(self.gd)

    def tearDown(self):
        self._t.cleanup()

    def plant(self, **over):
        rec = ML.owner("m9")
        rec.update(over)
        with open(self.path, "wb") as fh:
            fh.write(ML.MAGIC + json.dumps(rec).encode() + b"\n")
        return rec

    def verdict(self):
        rec, _why = ML.read_record(self.path)
        with mock.patch.object(ML, "network_fs", return_value=False):
            return ML.stale_verdict(self.path, rec)

    def test_a_lock_from_another_host_is_never_declared_dead(self):
        self.plant(host="some-other-machine")
        ok, why = self.verdict()
        self.assertFalse(ok)
        self.assertIn("another machine", why)

    def test_a_network_filesystem_refuses_loudly(self):
        rec = self.plant(pid=999999)
        for answer in (True, None):
            with self.subTest(answer), mock.patch.object(ML, "network_fs", return_value=answer):
                ok, why = ML.stale_verdict(self.path, rec)
                self.assertFalse(ok)
                self.assertIn("network filesystem", why)

    def test_a_recycled_pid_is_told_apart_by_its_start_time(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            exists, start = ML.pid_start(child.pid)
            if not exists or start is None:
                self.skipTest("this platform cannot read a process start time")
            self.plant(pid=child.pid, start=start)
            self.assertFalse(self.verdict()[0], "a LIVE owner was declared dead")
            self.plant(pid=child.pid, start="some earlier process")
            ok, why = self.verdict()
            self.assertTrue(ok)
            self.assertIn("different process", why)
        finally:
            child.kill()
            child.wait()

    def test_a_dead_pid_is_removable_and_a_live_merge_here_is_not(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.plant(pid=child.pid)
        self.assertTrue(self.verdict()[0])
        self.plant(pid=os.getpid(), merge="live")
        ML.mark_active("live", True)
        try:
            self.assertFalse(self.verdict()[0])
        finally:
            ML.mark_active("live", False)

    def test_a_reboot_proves_the_owner_gone(self):
        self.plant(boot="an-earlier-boot")
        if not ML.boot_id():
            self.skipTest("no boot id on this platform")
        self.assertTrue(self.verdict()[0])

    def test_removal_re_verifies_the_content_it_was_approved_for(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.plant(pid=child.pid)
        sha = ML.lock_sha(self.gd)
        self.plant(pid=child.pid, at=1.0)                      # changed after the approval
        with mock.patch.object(ML, "network_fs", return_value=False):
            removed, why = ML.remove_stale(self.gd, sha)
        self.assertFalse(removed)
        self.assertTrue(os.path.exists(self.path))
        with open(self.path, "wb") as fh:
            fh.write(b"git's own lock\n")
        removed, why = ML.remove_stale(self.gd, ML.lock_sha(self.gd))
        self.assertFalse(removed, "a lock that is not mokata's was removed")
        self.assertIn("not made by a mokata merge", why)


class RecoveryIsFromGitTruth(_Merge):

    def test_recovery_decides_from_git_truth_not_the_journal(self):
        """A crash before the CAS leaves nothing merged (and no lock); a crash after it — with the
        journal still saying `started` — is finished, because the BRANCH says it landed."""
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)

        def crash_before(step):
            if step == "s5":
                raise RuntimeError("power cut (injected)")
        with self.assertRaises(RuntimeError):
            MG.run_merge(self.main, pid, hooks=MG.Hooks(at=crash_before))
        self.assertFalse(os.path.exists(self.lock()))
        self.assertEqual(MG.NOT_MERGED, MG.recover(self.main, pid).state)

        wt2 = self.session(name="b", sid="S2", run="R2")
        self.evidence(wt2, run="R2")
        pid2, _p2 = self.approved(wt2)

        def crash_after(step):
            if step == "s6":
                raise RuntimeError("power cut after the commit point (injected)")
        with self.assertRaises(RuntimeError):
            MG.run_merge(self.main, pid2, hooks=MG.Hooks(at=crash_after))
        self.assertTrue(os.path.exists(self.lock()), "R1: lock dropped after the commit point")
        _state(self.main).update(MG.JOURNAL_PREFIX + pid2, lambda cur: dict(cur, phase="started"))
        unlock = MG.recover(self.main, pid2).facts["proposal_id"]
        approval.approve(self.main, unlock)
        done = MG.recover(self.main, pid2, unlock_proposal=unlock)
        self.assertEqual(MG.MERGED, done.state, done.message)
        self.assertEqual("B = 1\n", _read(self.main, "src/b.py"))

    def test_only_mokatas_own_dead_lock_is_removed_and_only_on_approval(self):
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)

        def at(step):
            if step == "s6-commit":
                raise OSError("injected")
        MG.run_merge(self.main, pid, hooks=MG.Hooks(at=at))
        with open(self.lock(), "wb") as fh:                     # git replaced it with its own
            fh.write(b"DIRC-ish foreign bytes")
        out = MG.recover(self.main, pid)
        self.assertEqual(MG.LOCK_HELD, out.state)
        self.assertIn("NOT mokata's", out.message)
        self.assertEqual(b"DIRC-ish foreign bytes", open(self.lock(), "rb").read())
        self.assertIn("git commit", out.message)             # the Recover help names the trap...
        self.assertIn(f"--recover {pid}", out.message)       # ...and the way out, with this id

    def test_every_refusal_to_remove_mokatas_lock_carries_the_recover_help(self):
        """Network fs, an unreadable boot id and another host each refuse to declare the owner gone.
        The user is then left holding a lock - and none of them may send them to a bare delete
        followed by `git commit`."""
        from unittest import mock
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)

        def at(step):
            if step == "s6-commit":
                raise OSError("injected")
        MG.run_merge(self.main, pid, hooks=MG.Hooks(at=at))
        for label, patch in (("network fs", mock.patch.object(MG.ML, "network_fs", return_value=True)),
                             ("boot id unreadable", mock.patch.object(MG.ML, "boot_id", return_value="")),
                             ("another host", mock.patch.object(MG.ML.socket, "gethostname",
                                                                return_value="some-other-host"))):
            with self.subTest(label), patch:
                out = MG.recover(self.main, pid)
                self.assertEqual(MG.LOCK_HELD, out.state, out.message)
                self.assertIn("cannot be removed", out.message)
                self.assertIn("git commit", out.message)
                self.assertIn(f"--recover {pid}", out.message)
                self.assertNotIn("Remove `.git/index.lock` yourself", out.message)

    def test_a_lock_deleted_by_hand_after_a_crash_is_finished_by_recover_not_by_git_commit(self):
        """The trap the help names: lock gone, branch AT the merge, index still at the base - so a
        plain `git commit` would revert the merge. Recover is the step that makes it whole."""
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)

        def at(step):
            if step == "s6-commit":
                raise OSError("injected")
        MG.run_merge(self.main, pid, hooks=MG.Hooks(at=at))
        os.remove(self.lock())                                  # the human deletes it by hand
        merged = self.head()
        self.assertNotEqual("", _git(self.main, "status", "--porcelain").strip(),
                            "the premise: HEAD at the merge, index at the base, so status is dirty")
        out = MG.recover(self.main, pid)
        self.assertEqual(MG.MERGED, out.state, out.message)
        self.assertEqual(merged, self.head())
        self.assertEqual("", _git(self.main, "status", "--porcelain").strip())
        self.assertEqual("A = 1\n", _read(self.main, "src/a.py"))

    def test_every_surface_that_offers_recover_names_the_git_commit_trap(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("docs/reference/cli.md", "src/mokata/ide/static/features/worktrees.js",
                    "src/mokata/cli_commands/runviews.py"):
            with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as fh:
                text = fh.read()
            with self.subTest(rel=rel):
                self.assertIn("git commit", text)
                self.assertRegex(text, r"(?is)(revert|undo).{0,40}merge|merge.{0,40}(revert|undo)|reverts the merge")
        self.assertIn("--recover", MG.HAND_DELETED_LOCK_HELP)
        self.assertIn("git commit", MG.HAND_DELETED_LOCK_HELP)


# ======================================================================= AC9–AC11: repo-chosen code
class TheRepoChoosesNoCode(_Merge):

    def marker_script(self, name):
        marker = os.path.join(self._t.name, name + ".ran")
        script = os.path.join(self._t.name, name + ".sh")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(f"#!/bin/sh\necho ran > '{marker}'\nexit 0\n")
        os.chmod(script, 0o755)
        return script, marker

    @unittest.skipIf(os.name == "nt", "shell hook scripts")
    def test_repo_hooks_and_fsmonitor_never_run_during_a_merge(self):
        wt = self.session()
        self.evidence(wt)
        hooks = os.path.join(self.main, ".git", "hooks")
        markers = []
        for h in ("pre-commit", "post-merge", "post-checkout", "reference-transaction",
                  "post-index-change"):
            script, marker = self.marker_script(h)
            os.replace(script, os.path.join(hooks, h))
            markers.append(marker)
        fsm, fsm_marker = self.marker_script("fsmonitor")
        _git(self.main, "config", "core.fsmonitor", fsm)
        markers.append(fsm_marker)
        pid, plan = self.approved(wt)
        self.assertIn("pre-commit", plan.render())              # the card names what it skips
        self.assertEqual(MG.MERGED, MG.run_merge(self.main, pid).state)
        for m in markers:
            self.assertFalse(os.path.exists(m), f"{os.path.basename(m)} ran")

    @unittest.skipIf(os.name == "nt", "shell hook scripts")
    def test_includes_and_git_env_cannot_redirect_or_run_code(self):
        wt = self.session()
        self.evidence(wt)
        evil_hooks = os.path.join(self._t.name, "evil-hooks")
        os.makedirs(evil_hooks)
        script, marker = self.marker_script("evil")
        os.replace(script, os.path.join(evil_hooks, "post-merge"))
        inc = os.path.join(self._t.name, "evil.gitconfig")
        with open(inc, "w", encoding="utf-8") as fh:
            fh.write(f"[core]\n\thooksPath = {evil_hooks}\n")
        _git(self.main, "config", "include.path", inc)
        pid, _p = self.approved(wt)
        with mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(self._t.name, "nowhere"),
                                          "GIT_INDEX_FILE": os.path.join(self._t.name, "i"),
                                          "GIT_CONFIG_PARAMETERS": f"'core.hookspath={evil_hooks}'"}):
            out = MG.run_merge(self.main, pid)
        self.assertEqual(MG.MERGED, out.state, out.message)
        self.assertFalse(os.path.exists(marker))

    def test_filters_are_judged_from_the_merged_tree_and_refused(self):
        wt = self.session(files={".gitattributes": "*.dat filter=evil\n", "x.dat": "data\n"})
        _git(self.main, "config", "filter.evil.clean", "cat")
        _git(self.main, "config", "filter.evil.smudge", "cat")
        plan = MG.plan_merge(self.main, wt)
        self.assertEqual(MG.NEEDS_FILTER, plan.state, plan.render())
        self.assertIn("evil", plan.reason())

    @unittest.skipIf(os.name == "nt", "shell signing script")
    def test_signing_programs_never_run_and_unsigned_is_disclosed(self):
        wt = self.session()
        self.evidence(wt)
        script, marker = self.marker_script("gpg")
        _git(self.main, "config", "commit.gpgSign", "true")
        _git(self.main, "config", "gpg.program", script)
        pid, plan = self.approved(wt)
        self.assertIn("unsigned", plan.render())
        self.assertEqual(MG.MERGED, MG.run_merge(self.main, pid).state)
        self.assertFalse(os.path.exists(marker))

    def test_the_committer_identity_is_shown_and_bound(self):
        wt = self.session()
        self.evidence(wt)
        plan = MG.plan_merge(self.main, wt)
        self.assertIn("t <t@t>", plan.render())
        self.assertEqual("t <t@t>", plan.args["author"])
        pid, _p = self.approved(wt)
        _git(self.main, "config", "user.name", "someone else")
        self.assertNotEqual(MG.MERGED, MG.run_merge(self.main, pid).state,
                            "an identity change after approval still merged")

    def test_git_binary_is_never_resolved_from_the_repo(self):
        with tempfile.TemporaryDirectory() as repo:
            for name in ("git", "git.exe"):
                with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
                    fh.write("#!/bin/sh\necho planted\n")
                os.chmod(os.path.join(repo, name), 0o755)
            for path in (repo, "", ".", os.pathsep.join(["", ".", repo])):
                with self.subTest(path=path):
                    got = resolve_git(repo, {"PATH": path})
                    self.assertTrue(got is None or not os.path.realpath(got).startswith(
                        os.path.realpath(repo)), got)
            got = resolve_git(repo, {"PATH": repo, "PATHEXT": ".EXE"}, nt=True)
            self.assertIsNone(got)

    @unittest.skipIf(os.name == "nt", "shell stub")
    def test_old_git_blocks_naming_its_version(self):
        with tempfile.TemporaryDirectory() as d:
            stub = os.path.join(d, "oldgit")
            with open(stub, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\necho 'git version 2.39.5'\n")
            os.chmod(stub, 0o755)
            with self.assertRaises(GitSafeError) as cm:
                Git(d, exe=stub).__enter__()
            self.assertEqual("git-too-old", cm.exception.code)
            self.assertIn("2.39.5", str(cm.exception))


# ======================================================================= AC12–AC14
class OneTargetOneBaseNothingDestroyed(_Merge):

    def test_worktree_aliases_resolve_to_one_identity_or_none(self):
        wt = self.session()
        with Git(self.main) as g:
            for spelling in (wt, wt + os.sep, os.path.join(wt, ".", ""), "worktree-a",
                             "refs/heads/worktree-a"):
                with self.subTest(spelling):
                    row, _why = MG.find_target(g, self.main, spelling)
                    self.assertIsNotNone(row)
                    self.assertTrue(os.path.samefile(row["worktree"], wt))
            self.assertIsNone(MG.find_target(g, self.main, self.main)[0], "main is not a target")
            self.assertIsNone(MG.find_target(g, self.main, "/no/such")[0])
        SW.bind(self.main, "S2", wt)
        run_intent.bind_registered(self.main, "S2", "R2")
        self.assertIn("ambiguous-run", {b["code"] for b in MG.plan_merge(self.main, wt).blocked})

    def test_two_sessions_merging_into_one_base_never_both_land(self):
        a = self.session(name="a", sid="S1", run="R1")
        b = self.session(name="b", sid="S2", run="R2")
        self.evidence(a, "R1")
        self.evidence(b, "R2")
        pa, _ = self.approved(a)
        pb, _ = self.approved(b)
        results = {}
        threads = [threading.Thread(target=lambda k, p: results.__setitem__(k, MG.run_merge(
            self.main, p)), args=(k, p)) for k, p in (("a", pa), ("b", pb))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        states = sorted(r.state for r in results.values())
        self.assertEqual(1, states.count(MG.MERGED), states)
        self.assertEqual("", _git(self.main, "status", "--porcelain",
                                  "--untracked-files=no").strip())

    def test_paths_a_checkout_would_silently_destroy_are_refused_by_name(self):
        wt = self.session(files={"README.md": "changed\n"})
        _git(self.main, "update-index", "--skip-worktree", "README.md")
        self.assertIn(MG.HIDDEN, {b["code"] for b in MG.plan_merge(self.main, wt).blocked})
        _git(self.main, "update-index", "--no-skip-worktree", "README.md")
        _git(self.main, "config", "core.sparseCheckout", "true")
        self.assertIn(MG.UNSUPPORTED_INDEX, {b["code"] for b in MG.plan_merge(self.main, wt).blocked})
        _git(self.main, "config", "--unset", "core.sparseCheckout")
        sub = self.session(name="g", sid="S3", run="R3")
        _git(sub, "update-index", "--add", "--cacheinfo", "160000," + self.head() + ",vendor/lib")
        _git(sub, "commit", "-qm", "a gitlink")
        self.assertIn(MG.SUBMODULE, {b["code"] for b in MG.plan_merge(self.main, sub).blocked})
        case = self.session(name="c", sid="S4", run="R4")
        blob = subprocess.run(["git", "-C", case, "hash-object", "-w", "--stdin"], input="other\n",
                              capture_output=True, text=True, check=True).stdout.strip()
        _git(case, "update-index", "--add", "--cacheinfo", "100644," + blob + ",readme.md")
        _git(case, "commit", "-qm", "a path that differs from README.md only by case")
        self.assertIn(MG.CASE, {b["code"] for b in MG.plan_merge(self.main, case).blocked})
        bad = self.session(name="w", sid="S5", run="R5")
        # Windows' git refuses the name `aux.txt` outright - not `git add`, not `update-index` - so a
        # commit that carries it can only come from another OS. Build that tree with plumbing
        # (`mktree` does not check names), exactly as such a commit arrives over a fetch.
        blob = subprocess.run(["git", "-C", bad, "hash-object", "-w", "--stdin"], input=b"x\n",
                              capture_output=True, check=True).stdout.strip()
        tree = subprocess.run(["git", "-C", bad, "ls-tree", "-z", "HEAD"], capture_output=True,
                              check=True).stdout + b"100644 blob " + blob + b"\taux.txt\0"
        new_tree = subprocess.run(["git", "-C", bad, "mktree", "-z"], input=tree,
                                  capture_output=True, check=True).stdout.decode().strip()
        commit = _git(bad, "commit-tree", new_tree, "-p", "HEAD", "-m",
                      "a path Windows cannot hold").strip()
        _git(bad, "update-ref", "HEAD", commit)
        self.assertIn(MG.UNPORTABLE, {b["code"] for b in MG.plan_merge(self.main, bad, nt=True).blocked})
        self.assertNotIn(MG.UNPORTABLE, {b["code"] for b in MG.plan_merge(self.main, bad, nt=False).blocked})


# ======================================================================= AC17–AC19: surfaces
class OneGateEverySurface(_Merge):

    def test_cli_mcp_and_ide_share_one_merge_gate(self):
        """The same merge proposed from the CLI, MCP and the IDE is ONE proposal id."""
        wt = self.session()
        self.evidence(wt)
        cli = MG.propose(self.main, MG.plan_merge(self.main, wt))["proposal_id"]
        from mokata.mcp.tools_worktree import worktree_merge
        mcp = worktree_merge(path=self.main, target=wt)["proposal_id"]
        ide = self.post(self.ide(), "/api/worktree/merge", {"path": wt})
        self.assertEqual("proposed", ide["stage"], ide)
        self.assertEqual({cli}, {mcp, ide["proposal_id"]})

    def test_an_mcp_merge_is_licensed_by_the_shared_mcp_consent_check(self):
        """P2: the MCP `worktree_merge` is redeemed through `mcp/consent._consent` — the same
        human-minted consent check every gated MCP write uses — exactly once, at the commit point,
        for this exact merge under the cross-surface run id. Its GRANTED is what licenses the merge."""
        from mokata.mcp import consent as CS
        from mokata.mcp import tools_worktree as TWT
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)
        with mock.patch.object(TWT, "_consent", wraps=CS._consent) as spy:
            r = TWT.worktree_merge(path=self.main, proposal_id=pid)
        self.assertTrue(r["committed"], r)
        self.assertEqual(1, spy.call_count)
        args, kw = spy.call_args
        self.assertEqual((MG.TOOL, pid), (args[1], args[3]))
        self.assertEqual(MG.APPROVAL_RUN, kw["run_id"])
        refused = approval.Consent(approval.REFUSED, code="read-only", reason="read-only (injected)")
        wt2 = self.session(name="b", sid="S2", run="R2")
        self.evidence(wt2, run="R2")
        pid2, _p2 = self.approved(wt2)
        tip = self.head()
        with mock.patch.object(TWT, "_consent", return_value=refused):
            r = TWT.worktree_merge(path=self.main, proposal_id=pid2)
        self.assertFalse(r["committed"], r)
        self.assertEqual(tip, self.head(), "a refused consent still merged")

    def test_merge_is_a_cancellable_job_with_a_cancel_point_per_step(self):
        """Cancel before the commit point: the job shows `cancelling`, then `cancelled`, and
        nothing changed. Once sealed (s5 on) Cancel is refused and the merge finishes."""
        from mokata.ide import jobs as J
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)
        base = self.head()
        reg = J.JobRegistry()
        gate, go = threading.Event(), threading.Event()

        def at(step):
            if step == "s2":
                gate.set()
                go.wait(30)
        job = reg.start("git.merge", {"p": pid}, lambda: MG.run_merge(
            self.main, pid, hooks=MG.Hooks(at=at), seal=J.seal_current).to_dict())
        self.assertTrue(gate.wait(30))
        reg.cancel(job.id)
        self.assertEqual(J.CANCELLING, job.state)
        go.set()
        self.assertTrue(job.finished.wait(60))
        self.assertEqual(J.CANCELLED, job.state)
        self.assertEqual(base, self.head())
        self.assertFalse(os.path.exists(self.lock()))
        self.assertTrue(MG.ready_to_run(self.main, pid)[0], "a cancelled merge burned its approval")

        sealed_seen = threading.Event()

        def at2(step):
            if step == "s5":
                sealed_seen.set()
                time.sleep(0.3)
        job2 = reg.start("git.merge", {"p": pid, "n": 2}, lambda: MG.run_merge(
            self.main, pid, hooks=MG.Hooks(at=at2), seal=J.seal_current).to_dict())
        self.assertTrue(sealed_seen.wait(60))
        self.assertFalse(job2.cancellable)
        reg.cancel(job2.id)
        self.assertTrue(job2.finished.wait(60))
        self.assertEqual(J.DONE, job2.state)
        self.assertTrue(job2.result["merged"], job2.result)
        reg.close()

    def test_the_ide_stopping_mid_merge_is_never_silent(self):
        """A shutdown waits for a sealed merge; an interrupted one is listed until recovered."""
        from mokata.ide import jobs as J
        wt = self.session()
        self.evidence(wt)
        pid, _p = self.approved(wt)
        reg = J.JobRegistry()
        sealed = threading.Event()

        def at(step):
            if step == "s6":
                sealed.set()
                time.sleep(0.5)
        job = reg.start("git.merge", {"p": pid}, lambda: MG.run_merge(
            self.main, pid, hooks=MG.Hooks(at=at), seal=J.seal_current).to_dict())
        self.assertTrue(sealed.wait(60))
        reg.close(wait=0.0)
        self.assertTrue(job.stopped.is_set(), "close() abandoned a sealed merge")
        self.assertTrue(job.result and job.result["merged"], job.result)
        rows = self.post(self.ide(), "/api/worktree/merge-recover", {})
        self.assertEqual("blocked", rows["stage"])
        listing = self.ide().get("/api/worktrees").json()
        self.assertEqual([], listing["interrupted"])

    def test_the_evidence_override_is_human_scoped_to_one_head_and_waives_only_no_run(self):
        wt = self.worktree("o")
        self.commit(wt, "src/o.py", "O = 1\n", "o")
        with self.assertRaises(MG.MergeGateError):
            MG.file_override(self.main, wt, "short", actor="jas")
        MG.file_override(self.main, wt, "a hotfix the run never covered", actor="jas")
        plan = MG.plan_merge(self.main, wt)
        self.assertTrue(plan.ok, plan.render())
        self.assertIn("WITHOUT run evidence", plan.render())
        _write(wt, "dirty.txt", "x\n")
        self.assertIn("dirty-worktree", {b["code"] for b in MG.plan_merge(self.main, wt).blocked})
        os.remove(os.path.join(wt, "dirty.txt"))
        self.commit(wt, "src/o2.py", "O2 = 1\n", "moved on")
        self.assertIn("no-run", {b["code"] for b in MG.plan_merge(self.main, wt).blocked})
        from mokata.cli_commands.runviews import cmd_worktree_merge_override
        import argparse
        with mock.patch("mokata.prompt._stdin_is_tty", return_value=False):
            rc = cmd_worktree_merge_override(argparse.Namespace(
                path=self.main, target=wt, reason="an agent trying it"))
        self.assertEqual(1, rc, "a non-TTY filed an override")
        self.assertIn("no-run", {b["code"] for b in MG.plan_merge(self.main, wt).blocked})

    def test_the_merge_commit_message_never_claims_evidence_that_is_not_there(self):
        """The pre-filled commit message's evidence line is built from the evidence present at the
        head: an override merge says "merged under override: <reason>" and names each unmet item
        (never "tests green"), and a blocked plan names its blockers. Only a clean plan says the
        tests were green and the review passed — and that is the message that lands."""
        wt = self.worktree("o")
        self.commit(wt, "src/o.py", "O = 1\n", "o")
        blocked = MG.plan_merge(self.main, wt)
        self.assertFalse(blocked.ok)
        msg = blocked.args["message"]
        self.assertNotIn("tests green", msg)
        self.assertIn("NOT ready", msg)
        self.assertIn("no-run", msg)
        MG.file_override(self.main, wt, "a hotfix the run never covered", actor="jas")
        plan = MG.plan_merge(self.main, wt)
        self.assertTrue(plan.ok, plan.render())
        msg = plan.args["message"]
        self.assertNotIn("tests green", msg)
        self.assertNotIn("review passed", msg)
        self.assertIn("merged under override", msg)
        self.assertNotIn("the tree that was tested and reviewed", plan.render())
        self.assertIn("NOT tested or reviewed by mokata (override)", plan.render())
        self.assertIn("a hotfix the run never covered", msg)
        for unmet in MG.OVERRIDE_UNMET:
            self.assertIn(unmet, msg)
        pid = MG.propose(self.main, plan)["proposal_id"]
        approval.approve(self.main, pid)
        self.assertEqual(MG.MERGED, MG.run_merge(self.main, pid).state)
        landed = _git(self.main, "log", "-1", "--format=%B")
        self.assertIn("merged under override", landed)
        self.assertNotIn("tests green", landed)
        clean = self.session(name="c", sid="S9", run="R9")
        self.evidence(clean, run="R9")
        self.assertIn("tests green and review passed", MG.plan_merge(self.main, clean).args["message"])

    def test_merge_evidence_is_a_p21_precondition_not_a_registered_gate(self):
        from mokata import gate_hook, skill_contracts
        self.assertNotIn("merge-evidence", skill_contracts.GATES)
        self.assertNotIn("merge-evidence", getattr(gate_hook, "OVERRIDABLE", ()))

    # CORPUS: THE WORKING TREE — the promise must be gone from the src/ that ships (sync-public
    # rsyncs the working tree), including a file not yet tracked.
    def test_the_old_merge_path_and_its_promise_are_gone(self):
        from mokata import worktree_ops
        for name in ("plan_merge", "merge", "MERGE_TIMEOUT", "TOOL_MERGE"):
            self.assertFalse(hasattr(worktree_ops, name), name)
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src",
                           "mokata")
        for dirpath, _dirs, files in os.walk(src):
            for f in files:
                if f.endswith((".py", ".md", ".js")):
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        text = fh.read()
                    with self.subTest(f):
                        self.assertNotIn("mokata never merges, so", text)
                        self.assertNotIn("then from the MAIN checkout run `git merge <branch>`", text)
        from mokata.ide.contract import route_index
        merge = [r for k, r in route_index().items() if "/api/worktree/merge" in k][0]
        self.assertIn('"started"', json.dumps(merge), "the merge route never starts a job")

class TheMergeRunsTheTestCommandTheWayThePreflightSaysItDoes(_Merge):
    """CI-FIX 0.0.27 round 5 (MB4-2). The gated merge's full-suite run and the IDE's Proof panel
    answer ONE question — can this test command be started, and how — with ONE resolver. On
    Windows that means: a bare name is found on absolute PATH entries only; npm/npx/pnpm/yarn run
    through node and their own JS entry; a `.cmd`/`.bat` (mvn.cmd, gradlew.bat) is never started
    and is said so, with the way past it; and nothing is ever handed to cmd.exe."""

    def command(self, cmd):
        mf = os.path.join(self.main, ".mokata", "manifest.json")
        with open(mf, encoding="utf-8") as fh:
            data = json.load(fh)
        data["settings"]["baseline"]["test_command"] = cmd
        with open(mf, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def path(self, *dirs):
        old = os.environ["PATH"]
        os.environ["PATH"] = os.pathsep.join(dirs)
        self.addCleanup(os.environ.__setitem__, "PATH", old)

    def tool_dir(self):
        d = tempfile.mkdtemp(prefix="mk-p21-tools-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, True))
        return os.path.realpath(d)

    def windows_path(self, prefix):
        import shutil
        node, git = shutil.which("node"), shutil.which("git")
        if not node:
            self.skipTest("no Node on this machine")
        return (prefix, os.path.dirname(node), os.path.dirname(git),
                os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32"))

    def fake_npm(self, prefix, mark):
        _write(prefix, "npm.cmd", "@echo off\r\necho CMD>> \"%s\"\r\n" % mark)
        _write(prefix, "node_modules/npm/package.json",
               json.dumps({"name": "npm", "bin": {"npm": "bin/npm-cli.js"}}))
        _write(prefix, "node_modules/npm/bin/npm-cli.js",
               "require('fs').appendFileSync(%s, 'JS|' + process.argv.slice(2).join('|') + '\\n');"
               "process.exit(0);\n" % json.dumps(mark))

    @unittest.skipIf(os.name == "nt", "POSIX script")
    def test_a_bare_runner_on_the_users_path_is_started_by_its_absolute_path(self):
        bindir = self.tool_dir()
        mark = os.path.join(bindir, "ARGV0")
        _write(bindir, "mk-runner", f"#!/bin/sh\necho \"$0\" > '{mark}'\nexit 0\n")
        os.chmod(os.path.join(bindir, "mk-runner"), 0o755)
        self.command("mk-runner")
        self.path(bindir, "/usr/bin", "/bin")
        wt = self.session()
        res = MS.run_full_suite(self.main, wt, environ={"PATH": os.environ["PATH"]})
        self.assertTrue(res["passed"], res)
        with open(mark, encoding="utf-8") as fh:
            self.assertEqual(os.path.join(bindir, "mk-runner"), fh.read().strip())

    @unittest.skipUnless(os.name == "nt", "Windows: npm.cmd + the npm package layout, real node.exe")
    def test_npm_runs_through_node_in_the_merge_suite_never_through_its_cmd_shim(self):
        prefix = self.tool_dir()
        mark = os.path.join(prefix, "M.txt")
        self.fake_npm(prefix, mark)
        self.path(*self.windows_path(prefix))
        self.command("npm test")
        wt = self.session()
        res = MS.run_full_suite(self.main, wt)
        self.assertTrue(res["passed"], res)
        with open(mark, encoding="utf-8") as fh:
            self.assertEqual("JS|test\n", fh.read().replace("\r\n", "\n"), "ran through cmd.exe")

    @unittest.skipUnless(os.name == "nt", "Windows: a batch-only runner")
    def test_a_batch_only_runner_is_refused_honestly_and_the_way_past_it_is_named(self):
        prefix = self.tool_dir()
        mark = os.path.join(prefix, "M.txt")
        _write(prefix, "mvn.cmd", "@echo off\r\necho MVN>> \"%s\"\r\nexit /b 0\r\n" % mark)
        self.path(*self.windows_path(prefix))
        self.command("mvn -q test")
        wt = self.session()
        with self.assertRaises(MS.SuiteError) as caught:
            MS.run_full_suite(self.main, wt)
        why = str(caught.exception)
        self.assertIn("mvn.cmd", why)
        self.assertIn("gated merge is not possible", why)
        self.assertNotIn("merge-override", why)
        self.assertIn("macOS, Linux or WSL", why)
        self.assertNotIn("test id", why)
        self.assertFalse(os.path.exists(mark), "the batch file was started")

    @unittest.skipUnless(os.name == "nt", "Windows: cmd.exe's `&`")
    def test_a_second_command_after_an_ampersand_never_runs(self):
        prefix = self.tool_dir()
        self.fake_npm(prefix, os.path.join(prefix, "M.txt"))
        self.path(*self.windows_path(prefix))
        injected = os.path.join(prefix, "INJECTED_DIR")
        self.command("npm test & mkdir " + injected)
        wt = self.session()
        with self.assertRaises(MS.SuiteError):
            MS.run_full_suite(self.main, wt)
        self.assertFalse(os.path.exists(injected), "cmd.exe ran the second command")


if __name__ == "__main__":
    unittest.main()
