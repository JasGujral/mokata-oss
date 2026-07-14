"""Stage 51 — git-worktree isolation for parallel/fanout tasks + paused/WIP sessions.

Drives the WorktreeManager directly (with a fake git that mirrors create/remove on the
filesystem, so orphan checks are real), the real-git path on a temp repo, the orchestrator
opt-in seam, the degrade-clean fallbacks, and the Stage-50 session tie-in. Both jsonschema
states; no real parallel harness needed.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from _support import sample_manifest_data  # noqa: F401  (path fix side-effect)

from mokata.execmode import PARALLEL, ExecutionChoice, Task, TaskResult, run_tasks
from mokata.govern import AuditLedger
from mokata.worktree import (
    GitResult,
    Worktree,
    WorktreeManager,
    session_worktree_label,
)


class FakeGit:
    """A minimal git double — mirrors worktree add/remove on the real filesystem so
    existence/orphan assertions are meaningful; status reports clean unless a path is dirty."""

    def __init__(self, is_repo=True, dirty=None):
        self.is_repo = is_repo
        self.dirty = set(dirty or [])
        self.calls = []

    def __call__(self, args, cwd=None):
        self.calls.append((tuple(args), cwd))
        head = args[:2]
        if head == ["rev-parse", "--is-inside-work-tree"]:
            return GitResult(0, "true\n") if self.is_repo else GitResult(128, "", "not a repo")
        if head == ["worktree", "add"]:
            os.makedirs(args[-1], exist_ok=True)
            return GitResult(0)
        if head == ["status", "--porcelain"]:
            return GitResult(0, " M f.py\n" if cwd in self.dirty else "")
        if head == ["worktree", "remove"]:
            path = args[-1]
            if os.path.isdir(path):
                shutil.rmtree(path)
            return GitResult(0)
        if head == ["worktree", "prune"]:
            return GitResult(0)
        return GitResult(0)


def _kinds(ledger):
    return [e["kind"] for e in ledger.entries()]


class TestWorktreeManager(unittest.TestCase):
    def test_create_then_remove_clean_and_logged(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            mgr = WorktreeManager(d, ledger=led, git=FakeGit())
            self.assertTrue(mgr.available())
            wt = mgr.create("unit-1")
            self.assertIsNotNone(wt)
            self.assertTrue(os.path.isdir(wt.path))
            res = mgr.remove(wt)                       # clean -> removed, auto-cleaned
            self.assertTrue(res.removed)
            self.assertFalse(res.changed)
            self.assertFalse(os.path.exists(wt.path))
            self.assertIn("worktree_create", _kinds(led))
            self.assertIn("worktree_remove", _kinds(led))

    def test_isolated_cm_leaves_no_orphan(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = WorktreeManager(d, git=FakeGit())
            seen = {}
            with mgr.isolated("unit-2") as wt:
                self.assertIsNotNone(wt)
                self.assertTrue(os.path.isdir(wt.path))
                seen["path"] = wt.path
            self.assertFalse(os.path.exists(seen["path"]))   # auto-cleaned on exit

    def test_changed_worktree_kept_unless_forced(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = WorktreeManager(d, git=FakeGit())
            path = mgr._wt_path("unit-3")
            mgr._git.dirty.add(path)                  # simulate uncommitted work
            wt = mgr.create("unit-3")
            kept = mgr.remove(wt)                      # changed + not forced -> KEEP
            self.assertFalse(kept.removed)
            self.assertTrue(kept.changed)
            self.assertTrue(os.path.exists(wt.path))
            forced = mgr.remove(wt, force=True)        # throwaway scratch -> force-remove
            self.assertTrue(forced.removed)
            self.assertFalse(os.path.exists(wt.path))

    def test_not_a_git_repo_degrades_to_inplace(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = WorktreeManager(d, git=FakeGit(is_repo=False))
            self.assertFalse(mgr.available())
            self.assertIsNone(mgr.create("x"))
            with mgr.isolated("x") as wt:
                self.assertIsNone(wt)                  # in-place fallback, no crash

    def test_git_unavailable_degrades(self):
        def broken_git(args, cwd=None):
            return GitResult(127, "", "git: command not found")
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(WorktreeManager(d, git=broken_git).available())

    def test_disabled_by_config_degrades(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = WorktreeManager(d, git=FakeGit(), enabled=False)
            self.assertFalse(mgr.available())
            self.assertIsNone(mgr.create("x"))

    def test_session_worktree_persists_until_resume(self):
        # Stage 50 tie-in: a paused session's WIP lives in its own worktree (NOT auto-removed)
        # until the run is resumed.
        with tempfile.TemporaryDirectory() as d:
            mgr = WorktreeManager(d, git=FakeGit())
            wt = mgr.create(session_worktree_label("run-1"))
            self.assertIn("session-run-1", wt.path)
            self.assertTrue(os.path.isdir(wt.path))   # persists across the pause
            self.assertTrue(mgr.remove(wt).removed)    # removed on resume
            self.assertFalse(os.path.exists(wt.path))


class _CwdRunner:
    """A worktree-aware runner — records the cwd each task was handed."""

    def __init__(self):
        self.cwds = []

    def run(self, task, cwd=None):
        self.cwds.append(cwd)
        return TaskResult(task.id, True, "s", output="o", input_tokens=1,
                          output_tokens=1, seen_context=task.context)


class _PlainRunner:
    def run(self, task):
        return TaskResult(task.id, True, "s", output="o", input_tokens=1,
                          output_tokens=1, seen_context=task.context)


class TestOrchestratorSeam(unittest.TestCase):
    def test_parallel_tasks_run_in_worktrees_then_cleaned(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            mgr = WorktreeManager(d, ledger=led, git=FakeGit())
            runner = _CwdRunner()
            res = run_tasks([Task("a", "x", context="c"), Task("b", "y", context="c")],
                            ExecutionChoice(PARALLEL, isolation=True, fanout=True),
                            runner=runner, ledger=led, worktrees=mgr)
            self.assertEqual(len(res.results), 2)
            self.assertEqual(_kinds(led).count("worktree_create"), 2)
            self.assertEqual(_kinds(led).count("worktree_remove"), 2)
            self.assertTrue(all(c and "worktrees" in c for c in runner.cwds))  # ran isolated
            wt_dir = os.path.join(d, ".mokata", "temp_local", "worktrees")
            leftovers = os.listdir(wt_dir) if os.path.isdir(wt_dir) else []
            self.assertEqual(leftovers, [])                # no orphan worktrees

    def test_no_manager_is_exactly_today(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            res = run_tasks([Task("a", "x", context="c")],
                            ExecutionChoice(PARALLEL, isolation=True),
                            runner=_PlainRunner(), ledger=led)   # no worktrees=
            self.assertTrue(res.results[0].ok)
            self.assertNotIn("worktree_create", _kinds(led))

    def test_non_git_seam_degrades_to_inplace(self):
        with tempfile.TemporaryDirectory() as d:
            led = AuditLedger(os.path.join(d, "l.jsonl"))
            mgr = WorktreeManager(d, ledger=led, git=FakeGit(is_repo=False))
            res = run_tasks([Task("a", "x", context="c")],
                            ExecutionChoice(PARALLEL, isolation=True),
                            runner=_CwdRunner(), ledger=led, worktrees=mgr)
            self.assertTrue(res.results[0].ok)            # ran in-place, no crash
            self.assertNotIn("worktree_create", _kinds(led))


def _has_git():
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


@unittest.skipUnless(_has_git(), "git not available")
class TestRealGit(unittest.TestCase):
    def _repo(self, d):
        for args in (["init", "-q", d],
                     ["-C", d, "config", "user.email", "t@example.com"],
                     ["-C", d, "config", "user.name", "t"]):
            subprocess.run(["git", *args], check=True, capture_output=True)
        with open(os.path.join(d, "f.txt"), "w", encoding="utf-8") as fh:
            fh.write("hi\n")
        subprocess.run(["git", "-C", d, "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", d, "commit", "-qm", "init"], check=True,
                       capture_output=True)

    def test_real_worktree_create_and_remove_no_orphan(self):
        with tempfile.TemporaryDirectory() as d:
            self._repo(d)
            led = AuditLedger(os.path.join(d, ".mokata", "l.jsonl"))
            mgr = WorktreeManager(d, ledger=led)
            self.assertTrue(mgr.available())
            wt = mgr.create("real-1")
            self.assertIsNotNone(wt)
            inside = subprocess.run(
                ["git", "-C", wt.path, "rev-parse", "--is-inside-work-tree"],
                capture_output=True, text=True)
            self.assertIn("true", inside.stdout)          # a real linked worktree
            self.assertTrue(mgr.remove(wt).removed)
            self.assertFalse(os.path.exists(wt.path))
            listing = subprocess.run(["git", "-C", d, "worktree", "list"],
                                     capture_output=True, text=True).stdout
            self.assertNotIn("real-1", listing)            # no orphan registered


class TestProbeFailsClosed(unittest.TestCase):
    """D5 — an UNKNOWN worktree status must read as CHANGED, never as clean.

    The bug: `is_changed` read a failed `git status --porcelain` as UNCHANGED — both on the
    exception path (`except Exception: return False`) and on the non-exception path (`r.ok and ...`
    is False when git exits non-zero). `remove(force=False)` then DELETED a worktree that may have
    held uncommitted work, and ledgered `ok=True, changed=False` — a FALSE audit row asserting a
    cleanliness nobody had ever managed to observe. Its own docstring promised the opposite ("so
    cleanup doesn't silently discard work").

    The worst case of failing CLOSED is an orphan worktree the user deletes by hand. The worst case
    of the old direction was destroyed work. These pin the safe direction."""

    def _mgr(self, git, ledger=None):
        return WorktreeManager("/repo", ledger=ledger, git=git)

    def test_status_raising_reads_as_changed(self):
        def boom(args, cwd=None):
            if args[:1] == ["status"]:
                raise OSError("git went away")
            return GitResult(0, "true\n")
        self.assertTrue(self._mgr(boom).is_changed(Worktree("wt", "/repo/wt")))

    def test_status_exiting_nonzero_reads_as_changed(self):
        # The inversion that had NO exception at all: a non-zero git also read as "clean".
        self.assertTrue(self._mgr(_StatusFails()).is_changed(Worktree("wt", "/repo/wt")))

    def test_a_failed_probe_keeps_the_worktree_and_says_why(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = AuditLedger(os.path.join(d, "l.jsonl"))
            git = _StatusFails()
            res = self._mgr(git, ledger).remove(Worktree("wt", "/repo/wt"), force=False)

            self.assertFalse(res.removed)             # NOT deleted — work may be in there
            self.assertTrue(res.changed)              # unknown reads as changed
            self.assertEqual(git.removed, [])         # `git worktree remove` never ran
            row = [e for e in ledger.entries() if e.get("kind") == "worktree_remove"][-1]
            self.assertFalse(row["ok"])
            self.assertTrue(row["changed"])
            # the audit row separates "we could not tell" from "it was dirty" — different facts.
            self.assertIn("status probe failed", row["reason"])

    def test_a_dirty_worktree_still_reads_as_changed_not_probe_failed(self):
        def dirty(args, cwd=None):
            if args[:1] == ["status"]:
                return GitResult(0, " M file.py\n")
            return GitResult(0, "")
        with tempfile.TemporaryDirectory() as d:
            ledger = AuditLedger(os.path.join(d, "l.jsonl"))
            res = self._mgr(dirty, ledger).remove(Worktree("wt", "/repo/wt"), force=False)
            self.assertFalse(res.removed)
            row = [e for e in ledger.entries() if e.get("kind") == "worktree_remove"][-1]
            self.assertIn("changed", row["reason"])
            self.assertNotIn("probe failed", row["reason"])

    def test_force_still_removes_so_isolated_leaves_no_orphan(self):
        # `isolated()` force-removes throwaway task scratch. Fail-closed must not resurrect orphans.
        removed = []

        def failing(args, cwd=None):
            if args[:1] == ["status"]:
                raise OSError("no git")
            if args[:2] == ["worktree", "remove"]:
                removed.append(args[-1])
            return GitResult(0, "")
        res = self._mgr(failing).remove(Worktree("wt", "/repo/wt"), force=True)
        self.assertTrue(res.removed)
        self.assertEqual(removed, ["/repo/wt"])


class _StatusFails:
    """A git double whose `status` exits NON-ZERO (no exception) — the silent half of the bug."""

    def __init__(self):
        self.removed = []

    def __call__(self, args, cwd=None):
        if args[:1] == ["status"]:
            return GitResult(128, "", "fatal: not a work tree")
        if args[:2] == ["worktree", "remove"]:
            self.removed.append(args[-1])
        return GitResult(0, "")


if __name__ == "__main__":
    unittest.main()
