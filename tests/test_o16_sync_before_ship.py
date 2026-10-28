"""O16 — SYNC-BEFORE-SHIP (Jas 2026-10-27: "we should always first rebase the worktree from develop,
so we don't lose changes between worktree creation and the pull request").

A session worktree branches from the base at one moment; the base keeps moving. Landing the branch
as it is lands code that was tested against a base that no longer exists. Now:
  * the IDE refuses to MERGE a branch that is behind its base, and says to Sync first;
  * Sync rebases the worktree onto the base — gated like merge (propose → human approves → apply);
  * a conflicting rebase is ABORTED: the branch is exactly as it was, the files are named;
  * the ship skill does the same before ANY landing (merge or PR) and re-runs the suite after.
On REAL git, Claude Code-shaped worktrees (test_p31's fixture).
"""
from __future__ import annotations

import os
import unittest

import _support  # noqa: F401  (puts src/ on the path; runs the interpreter preflight)

from test_p31_session_worktrees import _git, _hook, _Repo, _write  # noqa: F401
from mokata import approval
from mokata.skills import WORKTREE_MERGE_READY_HANDOFF


class TheBaseMovedWhileTheWorktreeLived(_Repo):

    def _approve(self, c, action, body):
        r = self.post(c, f"/api/worktree/{action}", body)
        self.assertEqual("proposed", r["stage"], r)
        approval.approve(self.main, r["proposal_id"])            # what `mokata approve <id>` does
        return self.post(c, f"/api/worktree/{action}", {**body, "proposal_id": r["proposal_id"]})

    def test_a_branch_behind_its_base_is_synced_then_merged_and_nothing_is_lost(self):
        wt = self.worktree("a")
        _hook("session-start", {"session_id": "S1", "cwd": wt, "source": "startup"})
        mine = self.commit(wt, "src/feature.py", "F = 1\n", "the session's work")
        theirs = self.commit(self.main, "src/other.py", "O = 1\n", "landed on the base meanwhile")
        c = self.ide()

        r = self.post(c, "/api/worktree/merge", {"path": wt})
        self.assertEqual("blocked", r["stage"], r)
        self.assertIn("Sync it first", r["reason"])

        r = self._approve(c, "sync", {"path": wt})
        self.assertEqual("done", r["stage"], r)
        self.assertTrue(r["result"]["synced"], r)
        _git(wt, "merge-base", "--is-ancestor", theirs, "HEAD")   # the base's change is under it
        self.assertEqual("O = 1\n", open(os.path.join(wt, "src", "other.py"), encoding="utf-8").read())

        r = self._approve(c, "merge", {"path": wt})
        self.assertEqual("done", r["stage"], r)
        self.assertTrue(r["result"]["merged"], r)
        for path, text in (("src/feature.py", "F = 1\n"), ("src/other.py", "O = 1\n")):
            with self.subTest(path):
                with open(os.path.join(self.main, *path.split("/")), encoding="utf-8") as fh:
                    self.assertEqual(text, fh.read(), "a change was lost between branch and land")
        self.assertTrue(mine)

    def test_sync_waits_for_the_human(self):
        wt = self.worktree("a")
        self.commit(wt, "src/feature.py", "F = 1\n", "mine")
        self.commit(self.main, "src/other.py", "O = 1\n", "theirs")
        head = _git(wt, "rev-parse", "HEAD").strip()
        c = self.ide()
        r = self.post(c, "/api/worktree/sync", {"path": wt})
        self.assertEqual("proposed", r["stage"], r)
        r = self.post(c, "/api/worktree/sync", {"path": wt, "proposal_id": r["proposal_id"]})
        self.assertEqual(("refused", approval.REFUSED_NOT_APPROVED), (r["stage"], r["code"]))
        self.assertEqual(head, _git(wt, "rev-parse", "HEAD").strip(), "rebased without approval")

    def test_a_conflicting_rebase_is_aborted_and_the_branch_is_unchanged(self):
        wt = self.worktree("a")
        self.commit(wt, "src/app.py", "VALUE = 'mine'\n", "mine")
        self.commit(self.main, "src/app.py", "VALUE = 'theirs'\n", "theirs")
        head = _git(wt, "rev-parse", "HEAD").strip()
        c = self.ide()
        r = self._approve(c, "sync", {"path": wt})
        self.assertEqual("done", r["stage"], r)
        self.assertFalse(r["result"]["synced"], r)
        self.assertIn("src/app.py", r["result"]["reason"])
        self.assertEqual(head, _git(wt, "rev-parse", "HEAD").strip())
        self.assertFalse(os.path.isdir(os.path.join(_git(wt, "rev-parse", "--git-dir").strip(),
                                                    "rebase-merge")), "a rebase was left open")

    def test_a_dirty_worktree_or_an_up_to_date_one_is_not_synced(self):
        wt = self.worktree("a")
        self.commit(wt, "src/feature.py", "F = 1\n", "mine")
        c = self.ide()
        r = self.post(c, "/api/worktree/sync", {"path": wt})
        self.assertEqual("blocked", r["stage"])
        self.assertIn("already up to date", r["reason"])
        self.commit(self.main, "src/other.py", "O = 1\n", "theirs")
        _write(wt, "README.md", "dirty\n")
        r = self.post(c, "/api/worktree/sync", {"path": wt})
        self.assertEqual("blocked", r["stage"])
        self.assertIn("uncommitted", r["reason"])


class TheShipSkillSyncsBeforeAnyLanding(unittest.TestCase):

    def test_ship_rebases_onto_the_base_and_re_runs_the_suite_before_merge_or_pr(self):
        text = WORKTREE_MERGE_READY_HANDOFF
        for must in ("SYNC BEFORE YOU LAND", "git rebase", "RE-RUN the full test suite",
                     "--force-with-lease", "never `--skip`"):
            with self.subTest(must):
                self.assertIn(must, text)


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()
