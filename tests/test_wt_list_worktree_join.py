"""WT-LIST (FR-WT-1, 0.0.16) — read-only `mokata worktree list` + the `worktree_list` MCP tool.

WT.S1 taught mokata to OFFER a worktree and WT.S4 to BIND a run to one; neither taught it to LOOK.
`git worktree list` was called NOWHERE in the shipped tree, so a worktree whose window had exited —
or whose branch was already merged — was invisible. This stage is the eye, and only an eye:

  (a) `mokata worktree list` — a READ-ONLY CLI subcommand beside the existing `worktree create`.
  (b) `worktree_list` — the same JOIN as structured data, a registered READ tool. ONE source of
      truth (`build_worktree_report`), two surfaces, so they cannot disagree.
  (c) THE STALENESS VERDICT — a closed set, each pinned here by CONSTRUCTING the real condition
      against REAL git, never by mocking the answer: active / idle / no-session / merged, plus
      `main` (labelled, never judged) and `unknown` (the honest floor when the evidence itself
      could not be read — so a failed registry read is never reported as "no-session").
  (d) A DEFINITIVE EMPTY STATE + DEGRADE-CLEAN — "no worktrees" is a sentence, not a blank table,
      and not-a-git-repo / git-absent / an unreadable registry each exit cleanly with a reason.

Business-level asserts throughout: the text a human is handed, the verdict a constructed repo
earns, and the state of the REAL repo after a read — never an internal call count.
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session as S
from mokata import session_registry as SR
from mokata import session_worktree as SW
from mokata import worktree as WT
from mokata import worktree_list as WL
from mokata.config import Surface

_SRC = Path(__file__).resolve().parents[1] / "src" / "mokata"
DEAD_PID = 2 ** 30                      # no such process — what a window that exited leaves behind


# --------------------------------------------------------------------------- real-git helpers
def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def _git(d, *args):
    return subprocess.run(["git", "-C", d, *args], check=True,
                          capture_output=True, text=True).stdout


def _mokata_git_repo(d, branch="main"):
    """A mokata-initialised git repo whose default branch is DETERMINISTIC (plumbing, so this
    works whatever the host's `init.defaultBranch` is)."""
    from mokata.init import init_repo
    init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
    surface = Surface.load(d)
    _git(d, "init", "-q")
    _git(d, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "mokata")
    return surface


def _repo_shape(d):
    """The facts a READ must never change: the worktrees and the branches."""
    return (_git(d, "worktree", "list"), _git(d, "branch", "--list"))


def _kill_pid(surface, session_id):
    """Make a recorded session's window look EXITED — the real `idle` condition."""
    store = SR._registry_store(surface)
    blob = store.read(SR.SESSION_REGISTRY_KEY)
    blob["sessions"][session_id]["pid"] = DEAD_PID
    store.write(SR.SESSION_REGISTRY_KEY, blob)


def _row(report, label_or_branch):
    for w in report.worktrees:
        if label_or_branch in (w.branch, w.label) or w.path.endswith(label_or_branch):
            return w
    raise AssertionError(f"no worktree row for {label_or_branch!r} in "
                         f"{[(w.label, w.branch) for w in report.worktrees]}")


def run_cli(argv):
    buf = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO("")
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


# ======================================================================================
# 1 · THE GIT READS — porcelain parse + default branch + merged set (fake runner, no repo)
# ======================================================================================

class _FakeGit:
    """A git runner recording every argv it is handed, answering from a canned table."""

    def __init__(self, table=None, default=None):
        self.table = table or {}
        self.default = default if default is not None else WT.GitResult(1, "", "no")
        self.calls = []

    def __call__(self, args, cwd=None):
        self.calls.append(list(args))
        for prefix, result in self.table.items():
            if list(args)[:len(prefix)] == list(prefix):
                return result
        return self.default


PORCELAIN = """worktree /repo
HEAD aaaa1111
branch refs/heads/main

worktree /repo-auth
HEAD bbbb2222
branch refs/heads/auth
locked being reviewed

worktree /repo-detached
HEAD cccc3333
detached

worktree /repo-gone
HEAD dddd4444
branch refs/heads/gone
prunable gitdir file points to non-existent location
future-git-key something we have never seen
"""


class TestGitReads(_Base):

    def test_porcelain_parses_every_stanza_and_flags(self):
        wts = WT._parse_worktree_porcelain(PORCELAIN)
        self.assertEqual([w.path for w in wts],
                         ["/repo", "/repo-auth", "/repo-detached", "/repo-gone"])
        self.assertEqual([w.branch for w in wts], ["main", "auth", "", "gone"])
        self.assertTrue(wts[0].is_main, "git lists the MAIN worktree first")
        self.assertFalse(any(w.is_main for w in wts[1:]))
        self.assertTrue(wts[1].locked)
        self.assertTrue(wts[2].detached)
        self.assertTrue(wts[3].prunable)
        self.assertEqual(wts[0].head, "aaaa1111")

    def test_an_unknown_porcelain_key_is_ignored_not_an_error(self):
        """A newer git may add keys; a read-only lister must not break on one."""
        wts = WT._parse_worktree_porcelain(PORCELAIN)
        self.assertEqual(wts[3].branch, "gone")     # the row after the unknown key is intact

    def test_list_worktrees_reports_could_not_ask_as_None_never_empty(self):
        """None and [] mean DIFFERENT things — conflating them is the ambiguity FR-WT-1 removes."""
        self.assertIsNone(WT.list_worktrees("/nowhere", git=_FakeGit()))          # git said no

        def _raiser(args, cwd=None):
            raise OSError("git is gone")
        self.assertIsNone(WT.list_worktrees("/nowhere", git=_raiser))
        # …and a healthy repo with only its main checkout is [], not None.
        ok = _FakeGit({("worktree", "list"): WT.GitResult(0, "worktree /repo\nHEAD a\nbranch "
                                                             "refs/heads/main\n")})
        self.assertEqual(len(WT.list_worktrees("/repo", git=ok) or []), 1)

    def test_default_branch_prefers_the_remote_head_then_verified_local_names(self):
        remote = _FakeGit({("symbolic-ref",): WT.GitResult(0, "origin/trunk\n")})
        self.assertEqual(WT.default_branch("/r", git=remote), "trunk")

        local_main = _FakeGit({("rev-parse", "--verify", "--quiet", "refs/heads/main"):
                               WT.GitResult(0, "abc\n")})
        self.assertEqual(WT.default_branch("/r", git=local_main), "main")

        local_master = _FakeGit({("rev-parse", "--verify", "--quiet", "refs/heads/master"):
                                 WT.GitResult(0, "abc\n")})
        self.assertEqual(WT.default_branch("/r", git=local_master), "master")

    def test_default_branch_is_None_rather_than_guessed(self):
        """No origin/HEAD, no local main/master ⇒ None. P16: never a fabricated default."""
        self.assertIsNone(WT.default_branch("/r", git=_FakeGit()))

    def test_merged_branches_uses_format_not_the_marker_prefixed_listing(self):
        """`git branch --merged` prefixes a branch checked out in ANOTHER worktree with `+` —
        which is every branch this feature is about. `--format` is why the names come back clean."""
        fake = _FakeGit({("branch", "--merged"): WT.GitResult(0, "main\nauth\n")})
        self.assertEqual(WT.merged_branches("/r", "main", git=fake), {"main", "auth"})
        self.assertIn("--format=%(refname:short)", fake.calls[0])
        self.assertIsNone(WT.merged_branches("/r", "", git=fake), "no base ⇒ no merged set")


# ======================================================================================
# 2 · (c) THE STALENESS VERDICT — each condition CONSTRUCTED against real git
# ======================================================================================

@unittest.skipUnless(_git_available(), "git not available")
class TestVerdicts(_Base):

    def _repo(self, parent, branch="main"):
        d = os.path.join(parent, "repo")
        os.makedirs(d)
        return d, _mokata_git_repo(d, branch=branch)

    def _bound_worktree(self, surface, topic, run_id):
        """A worktree created through the ONE gated creator, bound to `run_id` (a LIVE pid)."""
        os.environ[S.SESSION_ID_ENV] = run_id
        S.reset_for_test()
        res = SW.create_worktree(surface, topic=topic, assume_yes=True, out=lambda _s: None)
        self.assertTrue(res.created, res.reason)
        return res

    def _diverge(self, wt, name="new.txt"):
        """Put a commit on this worktree's branch that is NOT in the default branch — so the
        `merged` verdict (which outranks both session verdicts) cannot mask what is under test."""
        Path(wt, name).write_text("work", encoding="utf-8")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-q", "-m", "wip")

    def test_the_main_checkout_is_labelled_and_never_given_a_stale_verdict(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            self._bound_worktree(surface, "auth rework", "run-alpha")
            report = WL.build_worktree_report(surface)
            main = [w for w in report.worktrees if w.is_main]
            self.assertEqual(len(main), 1)
            self.assertEqual(main[0].verdict, WL.VERDICT_MAIN)
            self.assertIn("never given a stale verdict", main[0].reason)
            self.assertEqual(main[0].label, "main")

    def test_active_a_live_session_is_bound_to_this_worktrees_branch(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            res = self._bound_worktree(surface, "auth rework", "run-alpha")
            self._diverge(res.path)
            row = _row(WL.build_worktree_report(surface), res.branch)
            self.assertEqual(row.verdict, WL.VERDICT_ACTIVE)
            self.assertEqual(row.session_id, "run-alpha")
            self.assertTrue(row.session_alive)
            self.assertEqual(row.scope, "auth rework")
            self.assertIn("live", row.reason)

    def test_idle_a_session_row_is_bound_but_its_window_has_exited(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            res = self._bound_worktree(surface, "auth rework", "run-alpha")
            self._diverge(res.path)
            _kill_pid(surface, "run-alpha")
            row = _row(WL.build_worktree_report(surface), res.branch)
            self.assertEqual(row.verdict, WL.VERDICT_IDLE)
            self.assertFalse(row.session_alive)
            self.assertIn("exited", row.reason)

    def test_no_session_the_worktree_exists_on_disk_with_nothing_bound(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            # a worktree cut OUTSIDE mokata — exactly the orphan class FR-WT-1 makes visible.
            _git(d, "worktree", "add", "-q", "-b", "orphan", os.path.join(parent, "orphan"))
            self._diverge(os.path.join(parent, "orphan"))
            row = _row(WL.build_worktree_report(surface), "orphan")
            self.assertEqual(row.verdict, WL.VERDICT_NO_SESSION)
            self.assertEqual(row.session_id, "")
            self.assertIsNone(row.session_alive)
            self.assertIn("no mokata session", row.reason)

    def test_merged_the_branch_is_already_in_the_default_branch(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            # a branch cut from main with nothing new on it: everything on it IS in main.
            _git(d, "worktree", "add", "-q", "-b", "done", os.path.join(parent, "done"))
            report = WL.build_worktree_report(surface)
            self.assertEqual(report.default_branch, "main")
            row = _row(report, "done")
            self.assertEqual(row.verdict, WL.VERDICT_MERGED)
            self.assertIn("already merged into 'main'", row.reason)

    def test_an_unmerged_branch_is_not_called_merged(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            wt = os.path.join(parent, "wip")
            _git(d, "worktree", "add", "-q", "-b", "wip", wt)
            Path(wt, "new.txt").write_text("work", encoding="utf-8")
            _git(wt, "add", "-A")
            _git(wt, "commit", "-q", "-m", "wip")
            row = _row(WL.build_worktree_report(surface), "wip")
            self.assertEqual(row.verdict, WL.VERDICT_NO_SESSION,
                             "unmerged + unbound is no-session, never merged")

    def test_the_default_branch_worktree_itself_is_never_called_merged(self):
        """A linked worktree checked out ON the default branch is not "merged work to discard"."""
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            _git(d, "worktree", "add", "-q", "--detach", os.path.join(parent, "det"))
            report = WL.build_worktree_report(surface)
            self.assertNotIn(WL.VERDICT_MERGED, [w.verdict for w in report.worktrees])

    def test_a_detached_worktree_says_so_and_is_never_merged(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            _git(d, "worktree", "add", "-q", "--detach", os.path.join(parent, "det"))
            row = _row(WL.build_worktree_report(surface), "det")
            self.assertTrue(row.detached)
            self.assertEqual(row.branch, "")
            self.assertEqual(row.verdict, WL.VERDICT_NO_SESSION)
            self.assertIn("detached HEAD", row.reason)


@unittest.skipUnless(_git_available(), "git not available")
class TestVerdictPrecedence(_Base):
    """More than one condition can hold at once; the order is active > merged > idle > no-session."""

    def _repo(self, parent, branch="main"):
        d = os.path.join(parent, "repo")
        os.makedirs(d)
        return d, _mokata_git_repo(d, branch=branch)

    def test_active_beats_merged_so_live_work_is_never_labelled_stale(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            res = SW.create_worktree(surface, topic="auth", assume_yes=True, out=lambda _s: None)
            self.assertTrue(res.created, res.reason)
            report = WL.build_worktree_report(surface)
            # the branch really IS merged (nothing new on it) — and the verdict is still `active`.
            self.assertIn(res.branch, WT.merged_branches(d, "main") or set())
            self.assertEqual(_row(report, res.branch).verdict, WL.VERDICT_ACTIVE)

    def test_merged_beats_idle_because_the_work_is_already_in(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            res = SW.create_worktree(surface, topic="auth", assume_yes=True, out=lambda _s: None)
            _kill_pid(surface, "run-alpha")
            row = _row(WL.build_worktree_report(surface), res.branch)
            self.assertEqual(row.verdict, WL.VERDICT_MERGED)
            # the idle FACT is not lost — it is still on the row, it just isn't the headline.
            self.assertEqual(row.session_id, "run-alpha")
            self.assertFalse(row.session_alive)

    def test_idle_beats_no_session_because_a_bound_row_is_more_specific(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            res = SW.create_worktree(surface, topic="auth", assume_yes=True, out=lambda _s: None)
            wt = SW.worktree_path_for(d, res.branch)
            Path(wt, "new.txt").write_text("work", encoding="utf-8")     # …so it isn't merged
            _git(wt, "add", "-A")
            _git(wt, "commit", "-q", "-m", "wip")
            _kill_pid(surface, "run-alpha")
            self.assertEqual(_row(WL.build_worktree_report(surface), res.branch).verdict,
                             WL.VERDICT_IDLE)

    def test_the_verdict_vocabulary_is_a_closed_set(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface = self._repo(parent)
            _git(d, "worktree", "add", "-q", "-b", "done", os.path.join(parent, "done"))
            report = WL.build_worktree_report(surface)
            for w in report.worktrees:
                with self.subTest(worktree=w.label):
                    self.assertIn(w.verdict, WL.VERDICTS)
                    self.assertTrue(w.reason, "every verdict names its evidence")
            self.assertEqual(report.to_dict()["verdicts"], list(WL.VERDICTS))


@unittest.skipUnless(_git_available(), "git not available")
class TestMergedIsNeverGuessed(_Base):

    def test_an_undeterminable_default_branch_yields_no_merged_verdict_and_says_why(self):
        """No origin/HEAD and no local main/master: `merged` is UNPROVABLE, so it is not claimed."""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d, branch="trunk")
            _git(d, "worktree", "add", "-q", "-b", "done", os.path.join(parent, "done"))
            report = WL.build_worktree_report(surface)
            self.assertIsNone(report.default_branch)
            self.assertEqual(report.merged_check, WL.MERGED_SKIPPED)
            self.assertNotIn(WL.VERDICT_MERGED, [w.verdict for w in report.worktrees])
            self.assertEqual(_row(report, "done").verdict, WL.VERDICT_NO_SESSION)
            self.assertIn("default branch could not be determined", report.render())


# ======================================================================================
# 3 · (d) THE DEFINITIVE EMPTY STATE + DEGRADE-CLEAN
# ======================================================================================

@unittest.skipUnless(_git_available(), "git not available")
class TestEmptyState(_Base):

    def test_a_repo_with_only_its_main_checkout_says_so_in_words(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            report = WL.build_worktree_report(surface)
            self.assertTrue(report.ok)
            self.assertTrue(report.empty)
            self.assertEqual(report.linked, [])
            self.assertEqual(len(report.worktrees), 1, "the main checkout is still listed")
            text = report.render()
            self.assertIn("no worktrees", text)
            self.assertIn("only its main checkout", text)
            self.assertIn("mokata worktree create", text, "it points at the ONE gated creator")

    def test_the_empty_state_is_explicit_on_both_surfaces(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            _mokata_git_repo(d)
            from mokata.mcp import tools_read as TR
            payload = TR.worktree_list(path=d)
            self.assertTrue(payload["ok"])
            self.assertIs(payload["empty"], True)
            self.assertEqual(payload["linked_count"], 0)
            self.assertIn("no worktrees", payload["message"])
            rc, out = run_cli(["worktree", "list", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("no worktrees", out)

    def test_empty_is_never_confused_with_degraded(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            self.assertTrue(WL.build_worktree_report(surface).empty)
            with tempfile.TemporaryDirectory() as bare:
                from mokata.init import init_repo
                init_repo(root=bare, profile="standard", assume_yes=True, out=lambda _: None)
                degraded = WL.build_worktree_report(Surface.load(bare))
                self.assertFalse(degraded.ok)
                self.assertFalse(degraded.empty, "a failed read is NOT an empty repo")


class TestDegradesClean(_Base):

    def _bare(self, d):
        from mokata.init import init_repo
        init_repo(root=d, profile="standard", assume_yes=True, out=lambda _: None)
        return Surface.load(d)

    def test_not_a_git_repo_gives_one_honest_line_and_a_clean_exit(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self._bare(d)
            report = WL.build_worktree_report(surface)
            self.assertFalse(report.ok)
            self.assertEqual(report.reason, WL.NOT_A_GIT_REPO)
            self.assertIn("not a git repository", report.render())
            rc, out = run_cli(["worktree", "list", "--path", d])
            self.assertEqual(rc, 0, "a read-only answer is not a command failure")
            self.assertIn("not a git repository", out)

    def test_git_absent_is_reported_as_such_not_as_no_worktrees(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d) if _git_available() else self._bare(d)
            if not _git_available():
                self.skipTest("git not available")

            def _no_git(args, cwd=None):
                raise OSError("[Errno 2] No such file or directory: 'git'")
            report = WL.build_worktree_report(surface, git=_no_git)
            self.assertFalse(report.ok)
            self.assertEqual(report.reason, WL.GIT_UNAVAILABLE)
            self.assertFalse(report.empty)

    @unittest.skipUnless(_git_available(), "git not available")
    def test_an_unreadable_registry_yields_unknown_never_a_false_no_session(self):
        """The honest floor: mokata does not report "nothing is bound" when it could not LOOK."""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            _git(d, "worktree", "add", "-q", "-b", "wip", os.path.join(parent, "wip"))
            wt = os.path.join(parent, "wip")
            Path(wt, "n.txt").write_text("x", encoding="utf-8")   # …unmerged, so `merged` can't mask
            _git(wt, "add", "-A")
            _git(wt, "commit", "-q", "-m", "wip")
            with mock.patch.object(SR, "list_sessions", side_effect=OSError("registry is gone")):
                report = WL.build_worktree_report(surface)
            self.assertTrue(report.ok, "a lost registry degrades the VERDICT, not the listing")
            self.assertFalse(report.registry_ok)
            row = _row(report, "wip")
            self.assertEqual(row.verdict, WL.VERDICT_UNKNOWN)
            self.assertNotEqual(row.verdict, WL.VERDICT_NO_SESSION)
            self.assertIn("could not be read", report.render())

    @unittest.skipUnless(_git_available(), "git not available")
    def test_git_evidence_still_stands_when_the_registry_is_gone(self):
        """`merged` is a pure git fact — it survives a registry that does not."""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            _git(d, "worktree", "add", "-q", "-b", "done", os.path.join(parent, "done"))
            with mock.patch.object(SR, "list_sessions", side_effect=OSError("registry is gone")):
                report = WL.build_worktree_report(surface)
            self.assertEqual(_row(report, "done").verdict, WL.VERDICT_MERGED)

    def test_no_surface_raises_on_a_bare_directory(self):
        with tempfile.TemporaryDirectory() as d:
            self._bare(d)
            from mokata.mcp import tools_read as TR
            payload = TR.worktree_list(path=d)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["worktrees"], [])
            self.assertIn("not a git repository", payload["message"])


# ======================================================================================
# 4 · READ-ONLY, END TO END — the constraint that defines this stage
# ======================================================================================

@unittest.skipUnless(_git_available(), "git not available")
class TestReadOnly(_Base):

    def test_neither_surface_changes_the_repo(self):
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            _git(d, "worktree", "add", "-q", "-b", "done", os.path.join(parent, "done"))
            before = _repo_shape(d)
            WL.build_worktree_report(surface)
            from mokata.mcp import tools_read as TR
            TR.worktree_list(path=d)
            rc, _out = run_cli(["worktree", "list", "--path", d])
            self.assertEqual(rc, 0)
            self.assertEqual(_repo_shape(d), before,
                             "a lister created, removed and pruned nothing")

    def test_a_prunable_worktree_is_reported_and_left_alone(self):
        """The exact temptation the stage forbids: `git worktree prune` is FR-WT-2/3, not this."""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            gone = os.path.join(parent, "gone")
            _git(d, "worktree", "add", "-q", "-b", "gone", gone)
            import shutil
            shutil.rmtree(gone)                       # the directory is gone; git's ref is not
            before = _git(d, "worktree", "list", "--porcelain")
            report = WL.build_worktree_report(surface)
            self.assertTrue(any(w.prunable for w in report.linked),
                            "the prunable worktree is REPORTED")
            self.assertEqual(_git(d, "worktree", "list", "--porcelain"), before,
                             "…and left exactly where it was")

    def test_the_lister_registers_no_session_and_leaves_the_rows_untouched(self):
        """Unlike `session_windows`, this surface does NOT `touch()` to self-register — a lister
        has no business adding itself to the list it is showing. (The registry READ it reuses,
        `list_sessions(prune=False)`, rides the shared locked read-modify-write and writes back
        IDENTICAL content; it invents no row and reaps none, which is the property that matters.)
        """
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            store = SR._registry_store(surface)
            SR.list_sessions(surface, prune=False)      # materialise the key: compare like for like
            before = store.read(SR.SESSION_REGISTRY_KEY)
            WL.build_worktree_report(surface)
            from mokata.mcp import tools_read as TR
            TR.worktree_list(path=d)
            self.assertEqual(store.read(SR.SESSION_REGISTRY_KEY), before)
            rows = [r for r in SR.list_sessions(surface, prune=False)
                    if r.session_id == "run-alpha"]
            self.assertEqual(rows, [], "reading the worktrees registered no window")

    def test_the_lister_never_reaps_the_dead_row_it_reports(self):
        """The WT.S4 lesson, applied: `list_sessions` PRUNES dead-pid rows by default, so a lister
        that used the default would report `idle` ONCE and then silently downgrade the same
        worktree to `no-session` for ever after."""
        with tempfile.TemporaryDirectory() as parent:
            d = os.path.join(parent, "repo")
            os.makedirs(d)
            surface = _mokata_git_repo(d)
            os.environ[S.SESSION_ID_ENV] = "run-alpha"
            S.reset_for_test()
            res = SW.create_worktree(surface, topic="auth", assume_yes=True, out=lambda _s: None)
            self.assertTrue(res.created, res.reason)
            Path(res.path, "n.txt").write_text("x", encoding="utf-8")   # …so `merged` can't mask
            _git(res.path, "add", "-A")
            _git(res.path, "commit", "-q", "-m", "wip")
            _kill_pid(surface, "run-alpha")
            for i in range(3):
                with self.subTest(read=i):
                    row = _row(WL.build_worktree_report(surface), res.branch)
                    self.assertEqual(row.verdict, WL.VERDICT_IDLE,
                                     "the verdict survived being read")

    def test_the_module_contains_no_mutating_git_verb(self):
        src = (_SRC / "worktree_list.py").read_text(encoding="utf-8")
        for verb in ('"add"', '"remove"', '"prune"', "worktree prune"):
            with self.subTest(verb=verb):
                self.assertNotIn(verb, src)
        self.assertNotIn("SR.touch", src)
        self.assertNotIn(".touch(", src)


# ======================================================================================
# 5 · (a)+(b) ONE JOIN, TWO SURFACES — single-sourced, and registered the G1 way
# ======================================================================================

@unittest.skipUnless(_git_available(), "git not available")
class TestBothSurfaces(_Base):

    def _repo_with_worktrees(self, parent):
        d = os.path.join(parent, "repo")
        os.makedirs(d)
        surface = _mokata_git_repo(d)
        os.environ[S.SESSION_ID_ENV] = "run-alpha"
        S.reset_for_test()
        res = SW.create_worktree(surface, topic="auth rework", assume_yes=True,
                                 out=lambda _s: None)
        self.assertTrue(res.created, res.reason)
        _git(d, "worktree", "add", "-q", "-b", "orphan", os.path.join(parent, "orphan"))
        return d, surface, res

    def test_the_mcp_tool_returns_the_same_join_the_cli_renders(self):
        with tempfile.TemporaryDirectory() as parent:
            d, surface, res = self._repo_with_worktrees(parent)
            from mokata.mcp import tools_read as TR
            payload = TR.worktree_list(path=d)
            rc, out = run_cli(["worktree", "list", "--path", d])
            self.assertEqual(rc, 0)
            self.assertEqual(payload["message"], WL.build_worktree_report(surface).render(),
                             "ONE source of truth — the tool serialises what the CLI renders")
            self.assertIn(payload["message"], out)
            verdicts = {w["branch"]: w["verdict"] for w in payload["worktrees"]}
            self.assertEqual(verdicts[res.branch], WL.VERDICT_ACTIVE)
            self.assertEqual(verdicts["orphan"], WL.VERDICT_MERGED)

    def test_the_cli_names_each_worktree_its_branch_its_verdict_and_its_path(self):
        with tempfile.TemporaryDirectory() as parent:
            d, _surface, res = self._repo_with_worktrees(parent)
            from mokata.session import short_id
            rc, out = run_cli(["worktree", "list", "--path", d])
            self.assertEqual(rc, 0)
            # Separator-agnostic for the PATH: `git worktree list --porcelain` reports POSIX
            # separators even on Windows (`C:/Users/…`), while `res.path` is built with
            # `os.path.join` and is native (`C:\Users\…`). Which convention git prints is git's
            # business, not a property this test is pinning — what it pins is that the worktree
            # IS named. Compared with both sides folded to `/`.
            def _sep(text: str) -> str:
                return text.replace("\\", "/")

            self.assertIn(_sep(res.path), _sep(out))
            # the SHORT id, exactly as `mokata windows` names a session — one id form, not two.
            for expected in (res.branch, "orphan", WL.VERDICT_ACTIVE,
                             short_id("run-alpha"), "auth rework"):
                with self.subTest(expected=expected):
                    self.assertIn(expected, out)

    def test_the_ascii_flag_drops_the_glyphs(self):
        with tempfile.TemporaryDirectory() as parent:
            d, _s, _r = self._repo_with_worktrees(parent)
            rc, out = run_cli(["worktree", "list", "--path", d, "--ascii"])
            self.assertEqual(rc, 0)
            self.assertNotIn("↳", out)

    def test_the_payload_is_json_serializable(self):
        import json
        with tempfile.TemporaryDirectory() as parent:
            d, _s, _r = self._repo_with_worktrees(parent)
            from mokata.mcp import tools_read as TR
            json.dumps(TR.worktree_list(path=d))

    def test_the_join_agrees_with_run_for_branch_on_every_row(self):
        """The single-sourcing that matters: the report's binding IS `run_for_branch`'s answer,
        so the list and the WT.S4 binding can never disagree."""
        with tempfile.TemporaryDirectory() as parent:
            d, surface, _res = self._repo_with_worktrees(parent)
            for w in WL.build_worktree_report(surface).linked:
                with self.subTest(branch=w.branch):
                    self.assertEqual(w.session_id or None,
                                     SW.run_for_branch(surface, w.branch))


class TestRegisteredTheSameWay(_Base):

    def test_worktree_list_is_a_registered_READ_tool(self):
        import mokata.mcp.tools_read  # noqa: F401  (registration side effect)
        from mokata.mcp.registry import read_tool_names, write_tool_names
        self.assertIn("worktree_list", read_tool_names())
        self.assertNotIn("worktree_list", write_tool_names())

    def test_it_is_exported_the_same_single_sourced_way_as_session_windows(self):
        from mokata.mcp import tools_read as TR
        self.assertIn("worktree_list", TR.__all__)
        self.assertIn("session_windows", TR.__all__)

    def test_the_command_now_has_an_in_harness_read_surface(self):
        from mokata import parity
        surface = parity.SURFACE_MATRIX["worktree"]
        self.assertEqual(surface.mcp_read, ("worktree_list",))
        self.assertEqual(surface.mcp_write, (), "the gated CREATE half stays out of the WriteGate")
        self.assertFalse(surface.exempt, "a command with an in-harness surface is not exempt")
        self.assertTrue(surface.covered)

    def test_parity_still_verifies(self):
        from mokata import parity
        self.assertTrue(parity.verify_parity().ok, parity.verify_parity().render())

    def test_the_cli_still_has_exactly_one_gated_creator(self):
        """WT-LIST adds a READ. It must not have added a second `git worktree add`."""
        src = (_SRC / "session_worktree.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('"worktree", "add"'), 1)
        wl = (_SRC / "worktree_list.py").read_text(encoding="utf-8")
        self.assertNotIn("worktree_path_for", wl)


# ======================================================================================
# 6 · NEGATIVES — reuse, not a parallel subsystem
# ======================================================================================

class TestReuseNotRebuild(_Base):

    def test_the_git_runner_is_the_one_worktree_py_already_uses(self):
        """No second way to spawn git: the list/default-branch/merged reads default to the SAME
        `_default_git` the manager's `_git` defaults to."""
        self.assertIs(WT.WorktreeManager("/r")._git, WT._default_git)
        src = (_SRC / "worktree_list.py").read_text(encoding="utf-8")
        self.assertNotIn("import subprocess", src, "worktree_list never spawns git itself")

    def test_the_join_reuses_the_registry_read_and_the_wt_s4_resolver(self):
        src = (_SRC / "worktree_list.py").read_text(encoding="utf-8")
        self.assertIn("list_sessions", src)
        self.assertIn("run_for_branch", src)
        self.assertIn("prune=False", src)         # a read must not reap what it read (WT.S4)

    def test_run_for_branch_still_reads_the_registry_itself_when_rows_are_not_supplied(self):
        """The `rows=` seam is additive — WT.S4's callers are unchanged."""
        import inspect
        sig = inspect.signature(SW.run_for_branch)
        self.assertIsNone(sig.parameters["rows"].default)
        self.assertEqual(list(sig.parameters)[:2], ["surface", "branch"])

    def test_the_labels_come_from_repo_identity_not_a_new_scheme(self):
        src = (_SRC / "worktree_list.py").read_text(encoding="utf-8")
        self.assertIn("worktree_label", src)
        self.assertIn("canonical_repo_root", src)


if __name__ == "__main__":
    unittest.main()
