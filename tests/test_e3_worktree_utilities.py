"""0.0.23 E3 — WT.S3 FR-WT-2/3/4/6: worktree clean · remove · new-worktree notice · doctor · MCP.

Business-level, against REAL git (skipped without it): the worktrees a human ends up with, the
text they are shown, and the refusals they get — never an internal call count.

  * clean   — removes ONLY merged + clean + not-live worktrees, prunes vanished ones, KEEPS the
              rest with a reason; previews and asks (fail-closed); the branch survives.
  * remove  — refuses the main checkout and a live session ALWAYS (even with --force), refuses
              dirty/unmerged unless --force.
  * MCP     — both tools PROPOSE and remove nothing until a human-minted approval is redeemed.
  * notice  — SessionStart names a worktree created since the last session, once; first sighting
              is a silent baseline.
  * doctor  — names a merged / vanished worktree with the fixing command; silent when healthy.
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import _support  # noqa: F401  (puts src/ on the path)

from mokata import approval
from mokata import session as S
from mokata import session_worktree as SW
from mokata import worktree_manage as WM
from mokata.config import Surface


def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def _git(d, *args):
    return subprocess.run(["git", "-C", d, *args], check=True,
                          capture_output=True, text=True).stdout


def _repo(parent):
    from mokata.init import init_repo
    d = os.path.join(parent, "repo")
    os.makedirs(d)
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_: None)
    _git(d, "init", "-q")
    _git(d, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "mokata")
    return d, Surface.load(d)


def _add_wt(d, parent, branch):
    path = os.path.join(parent, f"wt-{branch}")
    _git(d, "worktree", "add", "-q", "-b", branch, path)
    return path


def _commit(wt, name="new.txt"):
    Path(wt, name).write_text("work", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "wip")


def _worktree_paths(d):
    return [ln.split(" ", 1)[1] for ln in _git(d, "worktree", "list", "--porcelain").splitlines()
            if ln.startswith("worktree ")]


def _same(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def _has_wt(d, path):
    return any(_same(p, path) for p in _worktree_paths(d))


def run_cli(argv, stdin=""):
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with redirect_stdout(buf):
            from mokata.cli import main
            rc = main(argv)
    finally:
        sys.stdin = old
    return rc, buf.getvalue()


class _Base(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()
        SW.reset_offers()
        SW.reset_run_offers()

    tearDown = setUp


@unittest.skipUnless(_git_available(), "git not available")
class TestClean(_Base):
    def test_plan_removes_only_merged_clean_and_keeps_the_rest_with_a_reason(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = _repo(parent)
            merged = _add_wt(d, parent, "merged")
            unmerged = _add_wt(d, parent, "unmerged")
            _commit(unmerged)
            dirty = _add_wt(d, parent, "dirty")
            Path(dirty, "scratch.txt").write_text("x", encoding="utf-8")
            plan = WM.plan_worktree_clean(surface)
            by = {os.path.basename(i.path): i for i in plan.items}
            self.assertEqual("remove", by["wt-merged"].action)
            self.assertEqual("keep", by["wt-unmerged"].action)
            self.assertIn("not merged", by["wt-unmerged"].reason)
            self.assertIn("--force", by["wt-unmerged"].reason)
            self.assertEqual("keep", by["wt-dirty"].action)
            self.assertIn("uncommitted", by["wt-dirty"].reason)
            self.assertEqual(1, len(plan.actionable))
            d_ = plan.to_dict()
            self.assertEqual((3, 1, 2), (d_["total_count"], d_["actionable_count"],
                                         d_["kept_count"]))
            # building a plan changes nothing
            self.assertTrue(_has_wt(d, merged) and _has_wt(d, unmerged) and _has_wt(d, dirty))

    def test_cli_clean_previews_and_a_non_tty_declines_removing_nothing(self):
        with tempfile.TemporaryDirectory() as parent:
            d, _s = _repo(parent)
            merged = _add_wt(d, parent, "merged")
            rc, out = run_cli(["worktree", "clean", "--path", d])
            self.assertEqual(1, rc)
            self.assertIn("remove", out)
            self.assertIn("declined — nothing was removed", out)
            self.assertTrue(_has_wt(d, merged))

    def test_cli_clean_yes_removes_the_merged_worktree_and_keeps_the_branch(self):
        with tempfile.TemporaryDirectory() as parent:
            d, _s = _repo(parent)
            merged = _add_wt(d, parent, "merged")
            unmerged = _add_wt(d, parent, "unmerged")
            _commit(unmerged)
            rc, out = run_cli(["worktree", "clean", "--yes", "--path", d])
            self.assertEqual(0, rc, out)
            self.assertIn("1 removed", out)
            self.assertFalse(_has_wt(d, merged))
            self.assertFalse(os.path.isdir(merged))
            self.assertTrue(_has_wt(d, unmerged))
            self.assertIn("merged", _git(d, "branch", "--list", "merged"))

    def test_a_vanished_worktree_is_pruned(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = _repo(parent)
            gone = _add_wt(d, parent, "gone")
            _commit(gone)
            shutil.rmtree(gone)
            plan = WM.plan_worktree_clean(surface)
            self.assertEqual(["prune"], [i.action for i in plan.items])
            outcome = WM.apply_worktree_clean(surface, plan)
            self.assertTrue(outcome.ok)
            self.assertEqual(1, len(outcome.pruned))
            self.assertFalse(_has_wt(d, gone))

    def test_nothing_to_clean_is_a_definitive_sentence_and_exit_0(self):
        with tempfile.TemporaryDirectory() as parent:
            d, _s = _repo(parent)
            rc, out = run_cli(["worktree", "clean", "--path", d])
            self.assertEqual(0, rc)
            self.assertIn("nothing to clean", out)

    def test_not_a_git_repo_is_exit_2_with_a_reason(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata.init import init_repo
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_: None)
            rc, out = run_cli(["worktree", "clean", "--path", d])
            self.assertEqual(2, rc)
            self.assertIn("not a git repository", out)

    def test_apply_rechecks_a_worktree_that_became_dirty_after_the_preview(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = _repo(parent)
            merged = _add_wt(d, parent, "merged")
            plan = WM.plan_worktree_clean(surface)
            Path(merged, "late.txt").write_text("x", encoding="utf-8")
            outcome = WM.apply_worktree_clean(surface, plan)
            self.assertEqual([], outcome.removed)
            self.assertEqual(1, len(outcome.refused))
            self.assertTrue(_has_wt(d, merged))


@unittest.skipUnless(_git_available(), "git not available")
class TestRemove(_Base):
    def test_unmerged_is_refused_without_force_and_removed_with_it(self):
        with tempfile.TemporaryDirectory() as parent:
            d, _s = _repo(parent)
            wt = _add_wt(d, parent, "feat")
            _commit(wt)
            rc, out = run_cli(["worktree", "remove", wt, "--yes", "--path", d])
            self.assertEqual(1, rc)
            self.assertIn("not merged", out)
            self.assertTrue(_has_wt(d, wt))
            rc, out = run_cli(["worktree", "remove", "feat", "--force", "--yes", "--path", d])
            self.assertEqual(0, rc, out)
            self.assertFalse(_has_wt(d, wt))
            self.assertIn("feat", _git(d, "branch", "--list", "feat"), "branch is kept")

    def test_dirty_is_refused_without_force(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = _repo(parent)
            wt = _add_wt(d, parent, "feat")
            Path(wt, "scratch.txt").write_text("x", encoding="utf-8")
            outcome = WM.remove_worktree(surface, wt)
            self.assertFalse(outcome.ok)
            self.assertIn("uncommitted", outcome.refused[0]["reason"])
            self.assertTrue(_has_wt(d, wt))

    def test_main_checkout_and_unknown_target_are_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = _repo(parent)
            _row, why = WM.check_remove(surface, d, force=True)
            self.assertIn("main checkout", why)
            row, why = WM.check_remove(surface, "nope", force=True)
            self.assertIsNone(row)
            self.assertIn("no worktree of this repo matches", why)

    def test_a_live_session_is_refused_even_with_force(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = _repo(parent)
            os.environ[S.SESSION_ID_ENV] = "run-live"
            S.reset_for_test()
            res = SW.create_worktree(surface, topic="auth", assume_yes=True, out=lambda *_: None)
            self.assertTrue(res.created, res.reason)
            outcome = WM.remove_worktree(surface, res.path, force=True)
            self.assertFalse(outcome.ok)
            self.assertIn("live", outcome.refused[0]["reason"])
            self.assertTrue(_has_wt(d, res.path))
            self.assertEqual("keep", WM.plan_worktree_clean(surface).items[0].action)

    def test_cli_remove_non_tty_declines(self):
        with tempfile.TemporaryDirectory() as parent:
            d, _s = _repo(parent)
            wt = _add_wt(d, parent, "merged")
            rc, out = run_cli(["worktree", "remove", wt, "--path", d])
            self.assertEqual(1, rc)
            self.assertIn("declined", out)
            self.assertTrue(_has_wt(d, wt))


@unittest.skipUnless(_git_available(), "git not available")
class TestMcpIsGated(_Base):
    def _mint(self, path, proposal_id):
        approval.approve(path, proposal_id, actor="test-human")

    def test_worktree_remove_proposes_then_commits_only_with_a_human_approval(self):
        from mokata.mcp import tools_worktree as T
        with tempfile.TemporaryDirectory() as parent:
            d, _s = _repo(parent)
            wt = _add_wt(d, parent, "merged")
            first = T.worktree_remove(path=d, target=wt)
            self.assertEqual("proposed", first["status"])
            self.assertTrue(_has_wt(d, wt), "a proposal removes nothing")
            typed = T.worktree_remove(path=d, target=wt, approve=True)
            self.assertEqual("proposed", typed["status"], "approve=true is not consent")
            self.assertTrue(_has_wt(d, wt))
            self._mint(d, first["proposal_id"])
            done = T.worktree_remove(path=d, target=wt, proposal_id=first["proposal_id"])
            self.assertTrue(done.get("committed"), done)
            self.assertFalse(_has_wt(d, wt))

    def test_worktree_remove_refusals_never_propose(self):
        from mokata.mcp import tools_worktree as T
        with tempfile.TemporaryDirectory() as parent:
            d, _s = _repo(parent)
            wt = _add_wt(d, parent, "feat")
            _commit(wt)
            out = T.worktree_remove(path=d, target=wt)
            self.assertEqual("refused", out["status"])
            self.assertIn("not merged", out["message"])
            self.assertEqual("error", T.worktree_remove(path=d)["status"])

    def test_worktree_clean_proposes_and_a_changed_plan_cannot_redeem(self):
        from mokata.mcp import tools_worktree as T
        with tempfile.TemporaryDirectory() as parent:
            d, _s = _repo(parent)
            a = _add_wt(d, parent, "a")
            first = T.worktree_clean(path=d)
            self.assertEqual("proposed", first["status"])
            self.assertTrue(_has_wt(d, a))
            self._mint(d, first["proposal_id"])
            b = _add_wt(d, parent, "b")                        # the plan moved after approval
            moved = T.worktree_clean(path=d, proposal_id=first["proposal_id"])
            self.assertFalse(moved.get("committed"), moved)
            self.assertTrue(_has_wt(d, a) and _has_wt(d, b))

    def test_worktree_clean_noop(self):
        from mokata.mcp import tools_worktree as T
        with tempfile.TemporaryDirectory() as parent:
            d, _s = _repo(parent)
            self.assertEqual("noop", T.worktree_clean(path=d)["status"])

    def test_both_tools_are_registered_as_gated_writes(self):
        from mokata.mcp import registry as REG
        self.assertIn("worktree_clean", REG.write_tool_names())
        self.assertIn("worktree_remove", REG.write_tool_names())
        self.assertIn("worktree_list", REG.read_tool_names())


@unittest.skipUnless(_git_available(), "git not available")
class TestNewWorktreeNotice(_Base):
    def test_first_sighting_is_silent_then_a_new_worktree_is_named_once(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = _repo(parent)
            self.assertIsNone(WM.new_worktree_notice(surface))          # baseline
            self.assertIsNone(WM.new_worktree_notice(surface))          # unchanged
            _add_wt(d, parent, "feat")
            line = WM.new_worktree_notice(surface)
            self.assertIsNotNone(line)
            self.assertIn("new git worktree", line)
            self.assertIn("wt-feat", line)
            self.assertNotIn("\n", line)
            self.assertIsNone(WM.new_worktree_notice(surface), "said once")

    def test_it_spawns_no_subprocess(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = _repo(parent)
            WM.new_worktree_notice(surface)
            _add_wt(d, parent, "feat")
            from unittest import mock
            with mock.patch("subprocess.run", side_effect=AssertionError("no subprocess")), \
                    mock.patch("subprocess.Popen", side_effect=AssertionError("no subprocess")):
                self.assertIsNotNone(WM.new_worktree_notice(surface))

    def test_the_briefing_carries_it_inside_the_budget(self):
        import contextlib
        import json
        from mokata import hook_cli
        with tempfile.TemporaryDirectory() as parent:
            d, _surface = _repo(parent)

            def start():
                out = io.StringIO()
                saved, sys.stdin = sys.stdin, io.StringIO(json.dumps({"cwd": d}))
                try:
                    with contextlib.redirect_stdout(out):
                        hook_cli.main(["session-start"])
                finally:
                    sys.stdin = saved
                return json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]

            first = start()
            self.assertNotIn("new git worktree", first)
            _add_wt(d, parent, "feat")
            self.assertIn("new git worktree", start())
            self.assertNotIn("new git worktree", start())

    def test_not_a_git_repo_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            from mokata.init import init_repo
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_: None)
            self.assertIsNone(WM.new_worktree_notice(Surface.load(d)))


@unittest.skipUnless(_git_available(), "git not available")
class TestDoctor(_Base):
    def test_healthy_and_non_git_are_silent(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = _repo(parent)
            self.assertEqual([], WM.worktree_findings(surface))
        with tempfile.TemporaryDirectory() as d:
            from mokata.init import init_repo
            init_repo(root=d, profile="standard", assume_yes=True, out=lambda *_: None)
            self.assertEqual([], WM.worktree_findings(Surface.load(d)))

    def test_merged_and_vanished_are_named_with_the_fix_and_never_fail_doctor(self):
        from mokata.govern.doctor import diagnose
        with tempfile.TemporaryDirectory() as parent:
            d, surface = _repo(parent)
            _add_wt(d, parent, "merged")
            gone = _add_wt(d, parent, "gone")
            _commit(gone)
            shutil.rmtree(gone)
            codes = {f.code: f for f in WM.worktree_findings(surface)}
            self.assertIn("worktree-merged", codes)
            self.assertIn("worktree-prunable", codes)
            self.assertIn("mokata worktree clean", codes["worktree-prunable"].detail)
            self.assertTrue(all(f.severity != "error" for f in codes.values()))
            report = diagnose(surface)
            self.assertIn("worktree-merged", {f.code for f in report.findings})


if __name__ == "__main__":
    unittest.main()
