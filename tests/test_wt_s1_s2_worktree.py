"""WT.S1–S2 — worktree detect + human-gated offer + shared project identity (release 0.0.13).

The gap these tests pin: two Claude Code windows on ONE repo clobber each other on the WORKING
TREE (MS.S2 fixed the state layer; the users still collide on the files themselves). A git
worktree is the escape hatch — but bare Claude Code neither DETECTS the collision nor OFFERS it,
and if a user manually uses a worktree today the team `project` identity SPLITS (worktree path ≠
repo path → separate team memory), silently forking team knowledge.

  (a) WT.S1 repo identity: ONE canonical identity that is the SAME for the main checkout and every
      linked worktree of it (git-common-dir based, degrade-clean for non-git dirs).
  (b) WT.S1 detect: sibling detection (≥2 live sessions, same repo identity) works ACROSS worktrees
      — because the registry is anchored at the canonical repo root, not the per-worktree path.
  (c) WT.S1 offer: a ONE-TIME (per session) offer when a live sibling exists on the same repo; it
      names the sibling and NEVER creates anything; not for a single session; not twice.
  (d) WT.S1 create (human-gated): `worktree create` fail-closes without an explicit confirm; with
      confirm it runs `git worktree add`, records the scope, and its recommendation names the topic.
  (e) WT.S2 identity: two worktrees of one repo share ONE team `project` identity (the WT.S2 bug
      pinned dead) — proven on a REMOTELESS repo, the case that split before.
  (f) `mokata windows` + `session_windows` show worktree (relative path or "main") + scope.
  (g) Degrade + no-behaviour-change: a non-git dir never crashes and never offers; a single window
      flow is byte-identical; offer/windows output carries no DSN value or memory content.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import _support  # noqa: F401  (puts src/ on the path)

from mokata import session as S
from mokata import session_registry as SR
from mokata import repo_identity as RI
from mokata import session_worktree as SW
from mokata.config import Surface


# --------------------------------------------------------------------------- real-git helpers
def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def _git(d, *args):
    subprocess.run(["git", "-C", d, *args], check=True, capture_output=True, text=True)


def _init_remoteless_repo(d):
    """A REAL git repo with a commit and NO remote — the case whose project id split per worktree."""
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    _git(d, "commit", "-q", "--allow-empty", "-m", "root")


def _add_worktree(main, name):
    """`git worktree add` a linked worktree as a sibling dir; return its path."""
    path = os.path.join(os.path.dirname(os.path.abspath(main)), f"wt-{name}")
    _git(main, "worktree", "add", "-q", "-b", name, path)
    return path


def _mokata(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)
    return Surface.load(d)


def _mokata_git_repo(d):
    """A dir that is BOTH a mokata repo and a real remoteless git repo (manifest committed)."""
    surface = _mokata(d)
    _init_remoteless_repo(d)
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "mokata")
    return surface


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


def _live_sibling_entry(sid, repo_root, phase="brainstorm", scope=None):
    """A registry entry for a LIVE (this-process pid ⇒ alive) sibling window."""
    e = {"session_id": sid, "started_at": "2020-01-01T00:00:00Z", "pid": os.getpid(),
         "repo_root": repo_root, "last_seen": "2020-01-01T00:00:00Z", "phase": phase}
    if scope is not None:
        e["scope"] = scope
    return e


@unittest.skipUnless(_git_available(), "git not available")
class TestRepoIdentity(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def test_repo_identity_same_for_main_and_worktree(self):
        with tempfile.TemporaryDirectory() as parent:
            main = os.path.join(parent, "repo")
            os.makedirs(main)
            _init_remoteless_repo(main)
            wt = _add_worktree(main, "feature")
            # ONE canonical identity across the main checkout and its linked worktree.
            self.assertEqual(RI.repo_identity(main), RI.repo_identity(wt))
            self.assertEqual(RI.canonical_repo_root(main), RI.canonical_repo_root(wt))
            self.assertEqual(RI.worktree_label(main), "main")
            self.assertNotEqual(RI.worktree_label(wt), "main")

    def test_non_git_dir_degrades_clean(self):
        with tempfile.TemporaryDirectory() as d:
            # never crashes; identity is stable and path-based; label is "main".
            self.assertEqual(RI.repo_identity(d), RI.repo_identity(d))
            self.assertEqual(RI.canonical_repo_root(d), os.path.abspath(d))
            self.assertEqual(RI.worktree_label(d), "main")


@unittest.skipUnless(_git_available(), "git not available")
class TestWtRegression(unittest.TestCase):
    """test_wt_regression — the WT.S2 bug pinned dead."""

    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def test_two_worktrees_share_identity_registry_and_project(self):
        with tempfile.TemporaryDirectory() as parent:
            main = os.path.join(parent, "repo")
            os.makedirs(main)
            surface_main = _mokata_git_repo(main)
            wt = _add_worktree(main, "feature")
            surface_wt = Surface.load(wt)

            # (i) same canonical repo identity from both checkouts.
            self.assertEqual(RI.repo_identity(main), RI.repo_identity(wt))

            # (ii) the registry is SHARED: a window in the worktree and a window in the main
            # checkout see each other as siblings (registry anchored at the canonical repo root).
            os.environ[S.SESSION_ID_ENV] = "sess-main"
            S.reset_for_test()
            SR.touch(surface_main, phase="develop")
            os.environ[S.SESSION_ID_ENV] = "sess-wt"
            S.reset_for_test()
            SR.touch(surface_wt, phase="spec")

            ids_from_wt = {r.session_id for r in SR.list_sessions(surface_wt)}
            self.assertIn("sess-main", ids_from_wt)
            self.assertIn("sess-wt", ids_from_wt)

            sibs = SW.live_siblings(surface_wt)          # we are sess-wt; sess-main is the sibling
            self.assertEqual({s.session_id for s in sibs}, {"sess-main"})

            # (iii) team project identity is IDENTICAL from both worktrees (the WT.S2 bug).
            from mokata.project import project_id
            self.assertEqual(project_id(surface_main), project_id(surface_wt))


@unittest.skipUnless(_git_available(), "git not available")
class TestOfferHonesty(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        os.environ["MOKATA_SESSION_ID"] = "me"
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def _surface_with_sibling(self, d):
        surface = _mokata(d)                     # non-git mokata repo is enough for the registry
        SR.touch(surface, phase="develop")       # register self ("me")
        # plant a LIVE sibling on the SAME repo root.
        store = surface.state
        cur = store.read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}
        cur["sessions"]["other-window"] = _live_sibling_entry("other-window", os.path.abspath(d),
                                                              phase="brainstorm")
        store.write(SR.SESSION_REGISTRY_KEY, cur)
        return surface

    def test_offer_fires_once_with_a_live_sibling_and_never_creates(self):
        with tempfile.TemporaryDirectory() as d:
            surface = self._surface_with_sibling(d)
            seen = set()
            out = []
            fired = SW.emit_offer_once(surface, out=out.append, seen=seen)
            self.assertTrue(fired)
            self.assertIn("other", out[0])            # names the sibling short id
            self.assertIn("worktree", out[0].lower())
            # NEVER creates anything: no worktree dir, no git worktree.
            self.assertFalse(os.path.exists(os.path.join(d, "..", "wt-anything")))
            # fires ONCE per session — a second call is suppressed.
            out2 = []
            self.assertFalse(SW.emit_offer_once(surface, out=out2.append, seen=seen))
            self.assertEqual(out2, [])

    def test_no_offer_for_a_single_session(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _mokata(d)
            SR.touch(surface, phase="develop")        # only self; no sibling
            out = []
            self.assertFalse(SW.emit_offer_once(surface, out=out.append, seen=set()))
            self.assertEqual(out, [])

    def test_non_git_dir_never_crashes_offer(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _mokata(d)
            SR.touch(surface, phase="develop")
            # no sibling, non-git → silent, no raise.
            self.assertFalse(SW.emit_offer_once(surface, out=lambda _m: None, seen=set()))


@unittest.skipUnless(_git_available(), "git not available")
class TestGatedCreate(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        os.environ["MOKATA_SESSION_ID"] = "creator"
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def test_without_confirm_nothing_is_created(self):
        with tempfile.TemporaryDirectory() as parent:
            main = os.path.join(parent, "repo")
            os.makedirs(main)
            surface = _mokata_git_repo(main)
            before = set(os.listdir(parent))
            # non-interactive stdin (a StringIO isn't a TTY) ⇒ read_yes_no fail-closes to No,
            # deterministically, whatever the test harness's ambient stdin is.
            buf = io.StringIO()
            old = sys.stdin
            sys.stdin = io.StringIO("")
            try:
                with redirect_stdout(buf):
                    res = SW.create_worktree(surface, topic="payment-retries")
            finally:
                sys.stdin = old
            self.assertFalse(res.created)
            self.assertEqual(set(os.listdir(parent)), before)   # nothing on disk changed

    def test_with_confirm_creates_records_scope_and_recommends_topic(self):
        with tempfile.TemporaryDirectory() as parent:
            main = os.path.join(parent, "repo")
            os.makedirs(main)
            surface = _mokata_git_repo(main)
            res = SW.create_worktree(surface, topic="payment-retries", assume_yes=True,
                                     out=lambda _m: None)
            self.assertTrue(res.created)
            self.assertTrue(os.path.isdir(res.path))            # the worktree really exists
            self.assertIn("payment-retries", res.recommendation)  # topic-aware recommendation
            # the scope is recorded on our registry entry.
            entry = surface.state.read(SR.SESSION_REGISTRY_KEY)["sessions"]["creator"]
            self.assertEqual(entry.get("scope"), "payment-retries")

    def test_refuses_politely_in_a_non_git_dir(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _mokata(d)                    # mokata repo but NOT a git repo
            res = SW.create_worktree(surface, topic="whatever", assume_yes=True,
                                     out=lambda _m: None)
            self.assertFalse(res.created)
            self.assertIn("git", res.reason.lower())


@unittest.skipUnless(_git_available(), "git not available")
class TestWindowsSurfaces(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def test_cli_windows_shows_worktree_and_scope(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _mokata(d)
            base = surface.state
            base.write(SR.SESSION_REGISTRY_KEY, {"sessions": {"win-scoped": _live_sibling_entry(
                "win-scoped", os.path.abspath(d), phase="develop", scope="topic-x")}})
            rc, out = run_cli(["windows", "--path", d])
            self.assertEqual(rc, 0)
            self.assertIn("main", out.lower())       # worktree column ("main" for the main checkout)
            self.assertIn("topic-x", out)            # scope shown

    def test_mcp_session_windows_includes_worktree_and_scope(self):
        from mokata.mcp import tools_read as TR
        with tempfile.TemporaryDirectory() as d:
            surface = _mokata(d)
            surface.state.write(SR.SESSION_REGISTRY_KEY, {"sessions": {"w1": _live_sibling_entry(
                "w1", os.path.abspath(d), phase="spec", scope="scope-y")}})
            res = TR.session_windows(path=d)
            w = next(x for x in res["windows"] if x["session_id"] == "w1")
            self.assertIn("worktree", w)
            self.assertEqual(w.get("scope"), "scope-y")


class TestSecretSafety(unittest.TestCase):
    def setUp(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        os.environ["MOKATA_SESSION_ID"] = "me"
        S.reset_for_test()

    def tearDown(self):
        os.environ.pop(S.SESSION_ID_ENV, None)
        S.reset_for_test()

    def test_offer_and_windows_carry_no_dsn_value(self):
        with tempfile.TemporaryDirectory() as d:
            surface = _mokata(d)
            secret_pw = "s3cr3t" + "_pw"
            dsn = "postgres" + "://u:" + secret_pw + "@" + "db.internal:5432/app"
            os.environ["MOKATA_TEST_DSN"] = dsn
            try:
                SR.touch(surface, phase="develop", scope="my-topic")
                cur = surface.state.read(SR.SESSION_REGISTRY_KEY) or {"sessions": {}}
                cur["sessions"]["other"] = _live_sibling_entry("other", os.path.abspath(d))
                surface.state.write(SR.SESSION_REGISTRY_KEY, cur)
                msg = SW.offer_text_once(surface, seen=set()) or ""
                blob = msg + json.dumps(surface.state.read(SR.SESSION_REGISTRY_KEY))
                self.assertNotIn("postgres" + "://", blob)
                self.assertNotIn(secret_pw, blob)
            finally:
                os.environ.pop("MOKATA_TEST_DSN", None)


if __name__ == "__main__":
    unittest.main()
