"""WT-ROOT — mokata works INSIDE a git worktree, and stops silently classifying one as not-a-repo.

The gap these tests pin. `find_mokata_root` (and `config.find_project_root`) walk ancestors for
`.mokata/manifest.json` and know nothing about git worktrees, while two OTHER subsystems already do
(`knowledge/freshness.py` resolves the `gitdir:` a `.git` FILE points at; `selfprotect.py` tests
`exists` not `isdir` precisely because a worktree's `.git` is a file). Root resolution is the one
place that does not know, and it fails in TWO different directions:

  (a) manifest NOT committed — the linked worktree has no `.mokata/` at all, so `find_mokata_root`
      returns None and the gate takes its "not a mokata project" INSTANT EXIT 0. mokata classifies
      its own repository as not-a-mokata-repo, and says so in the voice of a fresh directory.
  (b) manifest COMMITTED (the normal case — only `temp_local/` is gitignored, so `manifest.json`
      and `constitution.md` are checked out into every worktree) — resolution "succeeds" to the
      WORKTREE, which then grows its own EMPTY `temp_local/`: a second memory store and a second
      audit ledger, forked silently. Worse than (a), because nothing errors.

The ruling this encodes (Jas, 2026-08-04): branches/worktrees are separate, MEMORY IS SHARED.
Repo-scoped, resolving to the main checkout: manifest · audit ledger · memory store · session
registry. Per-tree: the working tree, the branch, and the rest of `temp_local` (pipeline state,
knowledge indexes, caches) — everything keyed to paths in THAT specific tree.

  (c) ONE shared canonical-root resolver, in `repo_identity` beside the worktree knowledge that
      already lives there — `find_mokata_root` and `find_project_root` both inherit from it.
  (d) THE PIN: from inside a GENUINE linked worktree, mokata resolves the main checkout's
      `.mokata/`, and finds ONE memory store and ONE ledger — not a second empty pair.
  (e) the per-tree half of the ruling still holds: pipeline state stays local to the tree.
  (f) the silent None goes: a worktree of a repo that DOES have `.mokata/`, where resolution still
      fails, SAYS so (stage 17's posture on `ledger=None`) instead of exiting 0 identically to
      "this was never a mokata repo".
  (g) no behaviour change anywhere else: a main checkout, a subdirectory, a non-git dir and a
      genuinely non-mokata repo all resolve exactly as before, and the non-mokata repo stays SILENT.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import _support  # noqa: F401  (puts src/ on the path)

from mokata import repo_identity as RI
from mokata import session_registry as SR
from mokata.config import Surface, find_project_root
from mokata.gate_hook import find_mokata_root
from mokata.govern.ledger import AuditLedger


# --------------------------------------------------------------------------- real-git helpers
def _git_available() -> bool:
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def _git(d, *args):
    subprocess.run(["git", "-C", d, *args], check=True, capture_output=True, text=True)


def _init_repo(d):
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    _git(d, "commit", "-q", "--allow-empty", "-m", "root")


def _mokata(d, profile="standard"):
    from mokata.init import init_repo
    init_repo(root=d, profile=profile, assume_yes=True, out=lambda _: None)


def _add_worktree(main, name):
    """A REAL `git worktree add` — the condition being fixed, never a hand-built stand-in."""
    path = os.path.join(os.path.dirname(os.path.abspath(main)), f"wt-{name}")
    _git(main, "worktree", "add", "-q", "-b", name, path)
    return path


def _commit_mokata_config(main):
    """Commit `.mokata/manifest.json` + `constitution.md` — the NORMAL case (case (b) above):
    `.mokata/.gitignore` ignores only `temp_local/`, so the config is committable and every linked
    worktree gets a checked-out copy of it."""
    _git(main, "add", "-A", ".mokata")
    _git(main, "commit", "-qm", "mokata config")


@unittest.skipUnless(_git_available(), "git is required for real-worktree tests")
class _WorktreeCase(unittest.TestCase):
    """A real mokata repo + a real linked worktree. `commit_config` picks case (a) or (b)."""

    commit_config = False

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main = os.path.join(self.tmp, "main")
        os.makedirs(self.main)
        _init_repo(self.main)
        _mokata(self.main)
        if self.commit_config:
            _commit_mokata_config(self.main)
        self.wt = _add_worktree(self.main, "feature")

    def assertSamePath(self, a, b, msg=None):
        self.assertEqual(os.path.realpath(a), os.path.realpath(b), msg)


# =============================================================== (c) the ONE shared resolver
class ResolverTests(_WorktreeCase):
    """(c) — one canonical-root resolver, in `repo_identity`, that both root-finders inherit."""

    def test_resolves_the_main_checkout_from_inside_a_linked_worktree(self):
        res = RI.resolve_mokata_root(self.wt)
        self.assertSamePath(res.root, self.main)
        self.assertTrue(res.via_worktree, "resolution went through the worktree redirect")

    def test_main_checkout_resolution_is_byte_identical(self):
        """(g) — the common case must not pay for the worktree case, nor change answer."""
        res = RI.resolve_mokata_root(self.main)
        self.assertSamePath(res.root, self.main)
        self.assertFalse(res.via_worktree)
        self.assertFalse(res.unresolved_worktree)

    def test_subdirectory_of_the_main_checkout_still_resolves_upward(self):
        sub = os.path.join(self.main, "a", "b")
        os.makedirs(sub)
        self.assertSamePath(RI.resolve_mokata_root(sub).root, self.main)

    def test_subdirectory_of_a_worktree_resolves_to_the_main_checkout(self):
        sub = os.path.join(self.wt, "a", "b")
        os.makedirs(sub)
        self.assertSamePath(RI.resolve_mokata_root(sub).root, self.main)


class ResolverCommittedConfigTests(ResolverTests):
    """Case (b): the manifest IS committed, so the worktree holds its own checked-out copy. The
    resolver must still name the MAIN checkout — the copy in the tree is config, not a state root."""

    commit_config = True


# =============================================================== the two root-finders inherit it
class RootFinderTests(_WorktreeCase):
    def test_find_mokata_root_returns_the_main_checkout_from_a_worktree(self):
        self.assertSamePath(find_mokata_root(self.wt), self.main)

    def test_find_project_root_returns_the_main_checkout_from_a_worktree(self):
        self.assertSamePath(find_project_root(self.wt), self.main)

    def test_the_two_finders_agree(self):
        """`find_mokata_root` is documented as the cheap twin of `find_project_root`, and pinned to
        it for initialized repos. A worktree must not be the place they disagree."""
        self.assertSamePath(find_mokata_root(self.wt), find_project_root(self.wt))


class RootFinderCommittedConfigTests(RootFinderTests):
    commit_config = True


# =============================================================== (d) THE PIN — one store, one ledger
class SharedRepoStateTests(_WorktreeCase):
    """(d) — the ruling's repo-scoped half, proven on a genuine linked worktree."""

    def test_surface_loads_from_inside_a_worktree(self):
        """Today this raises ConfigError('not initialized ... Run `mokata init` first') in case (a)
        — advice which, if followed, is what CREATES the second store this test forbids."""
        surface = Surface.load(self.wt)
        self.assertTrue(surface.manifest is not None)

    def test_one_audit_ledger_shared_with_the_main_checkout(self):
        here = AuditLedger.from_mokata_dir(Surface.load(self.wt).mokata_dir)
        there = AuditLedger.from_mokata_dir(Surface.load(self.main).mokata_dir)
        self.assertSamePath(here.path, there.path)
        self.assertIn(os.path.realpath(self.main), os.path.realpath(here.path),
                      "the ledger must live in the MAIN checkout, not the worktree")

    def test_an_entry_recorded_in_the_worktree_is_read_from_the_main_checkout(self):
        """Path equality is not the claim — a shared audit TRAIL is."""
        AuditLedger.from_mokata_dir(Surface.load(self.wt).mokata_dir).record(
            "test", note="written from the worktree")
        entries = AuditLedger.from_mokata_dir(Surface.load(self.main).mokata_dir).entries()
        self.assertTrue(any(e.get("note") == "written from the worktree" for e in entries),
                        "the main checkout cannot see what the worktree recorded")

    def test_one_memory_store_shared_with_the_main_checkout(self):
        from mokata.memory.selection import memory_dir_for
        self.assertSamePath(memory_dir_for(Surface.load(self.wt).mokata_dir),
                            memory_dir_for(Surface.load(self.main).mokata_dir))

    def test_one_session_registry_shared_with_the_main_checkout(self):
        """WT.S1 already anchored the registry at the canonical root — a regression guard, so the
        one thing that was right does not get re-broken by the resolver landing beside it."""
        self.assertSamePath(SR._registry_store(Surface.load(self.wt)).root,
                            SR._registry_store(Surface.load(self.main)).root)


class SharedRepoStateCommittedConfigTests(SharedRepoStateTests):
    """The case that forks silently today: the worktree HAS a `.mokata/`, so nothing errors — it
    just quietly grows a second empty memory store and a second empty ledger beside it."""

    commit_config = True

    def test_the_worktrees_own_temp_local_is_never_created(self):
        AuditLedger.from_mokata_dir(Surface.load(self.wt).mokata_dir).record("test", note="x")
        self.assertFalse(os.path.exists(os.path.join(self.wt, ".mokata", "temp_local")),
                         "a second state tree was created inside the worktree")


# =============================================================== (e) the per-tree half of the ruling
class RedirectRefusalTests(_WorktreeCase):
    """The redirect must never CONJURE a store. If the main checkout has no `.mokata/` of its own,
    the worktree's committed copy is all there is, and pointing the ledger and the memory store at a
    directory the user never initialized would invent state in a place they have never looked."""

    commit_config = True

    def setUp(self):
        super().setUp()
        shutil.rmtree(os.path.join(self.main, ".mokata"))   # only the worktree's copy survives

    def test_resolution_falls_back_to_the_trees_own_copy(self):
        self.assertSamePath(RI.resolve_mokata_root(self.wt).root, self.wt)

    def test_the_ledger_is_not_redirected_into_a_non_existent_main_checkout(self):
        led = AuditLedger.from_mokata_dir(os.path.join(self.wt, ".mokata"))
        self.assertSamePath(os.path.join(self.wt, ".mokata"),
                            os.path.dirname(os.path.dirname(os.path.dirname(led.path))))
        self.assertFalse(os.path.exists(os.path.join(self.main, ".mokata")),
                         "the redirect invented a `.mokata/` in the main checkout")

    def test_the_memory_store_is_not_redirected_either(self):
        from mokata.memory.selection import memory_dir_for
        self.assertSamePath(memory_dir_for(os.path.join(self.wt, ".mokata")),
                            os.path.join(self.wt, ".mokata", "temp_local", "memory"))


class PerTreeStateTests(_WorktreeCase):
    commit_config = True

    def test_pipeline_state_stays_local_to_the_tree(self):
        """Branches/worktrees are SEPARATE. Only the four named things are repo-scoped; the rest of
        `temp_local` is keyed to paths in this tree and must not follow the memory store home."""
        self.assertNotEqual(os.path.realpath(Surface.load(self.wt).temp_local_dir),
                            os.path.realpath(Surface.load(self.main).temp_local_dir))

    def test_the_working_tree_stays_the_worktree(self):
        self.assertSamePath(Surface.load(self.wt).root, self.wt)


# =============================================================== (f) the silent None goes
class UnresolvedWorktreeSpeaksTests(_WorktreeCase):
    """(f) — stage 17's posture on `ledger=None`, applied here. A worktree of a repo that DOES have
    `.mokata/`, where resolution still fails, must not be indistinguishable from a fresh dir."""

    def setUp(self):
        super().setUp()
        # The main checkout keeps its `.mokata/` (so this IS visibly a mokata repo) but the manifest
        # is gone — resolution genuinely cannot succeed, and that is exactly what must be SAID.
        os.remove(os.path.join(self.main, ".mokata", "manifest.json"))

    def test_resolution_reports_the_unresolved_worktree(self):
        res = RI.resolve_mokata_root(self.wt)
        self.assertIsNone(res.root)
        self.assertTrue(res.unresolved_worktree,
                        "an unresolvable worktree of a mokata repo must be distinguishable")

    def test_the_gate_hook_stays_silent_and_side_effect_free(self):
        """The hook is a FRESH PROCESS per native tool call, so `note_degraded`'s per-process memory
        would make this fire on every Write and every Edit, forever. It returns None as before; the
        voice lives on the surfaces below, where "once per session" is actually once."""
        from mokata.degrade import emitted_notices, reset_degrade_notices
        reset_degrade_notices()
        self.addCleanup(reset_degrade_notices)
        self.assertIsNone(find_mokata_root(self.wt))
        self.assertEqual([], emitted_notices())

    def test_surface_load_names_the_worktree_instead_of_advising_mokata_init(self):
        """`Run \\`mokata init\\` first` is the generic message, and it is the ONE piece of advice
        that must never be given here: running it inside a linked worktree is precisely what forks
        the memory store and the audit ledger."""
        from mokata.config import ConfigError
        with self.assertRaises(ConfigError) as ctx:
            Surface.load(self.wt)
        msg = str(ctx.exception)
        self.assertIn("worktree", msg.lower())
        self.assertNotIn("Run `mokata init` first", msg)

    def test_session_start_names_the_worktree_instead_of_offering_init(self):
        """The reachable surface, and the one that did the most damage. SessionStart resolves the
        root, finds no manifest, and used to emit the "let me initialize mokata" offer — advice
        which, followed inside a worktree, is what forks the memory store and the ledger."""
        from mokata.hook_cli import session_start_main
        payload = json.dumps({"hook_event_name": "SessionStart", "cwd": self.wt,
                              "source": "startup"})
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(payload)), redirect_stdout(buf):
            rc = session_start_main([])
        out = buf.getvalue()
        self.assertEqual(0, rc)
        self.assertIn("worktree", out.lower())
        self.assertNotIn("mokata init", out.replace(
            "Do NOT run `mokata init` here", ""), "the setup offer must not be made here")


# =============================================================== (g) nothing else changes
@unittest.skipUnless(_git_available(), "git is required")
class NoBehaviourChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_non_git_non_mokata_dir_resolves_to_none_silently(self):
        from mokata.degrade import emitted_notices, reset_degrade_notices
        reset_degrade_notices()
        self.addCleanup(reset_degrade_notices)
        self.assertIsNone(find_mokata_root(self.tmp))
        self.assertEqual([], emitted_notices(), "a plain directory must stay silent")

    def test_a_genuine_non_mokata_git_repo_resolves_to_none_silently(self):
        from mokata.degrade import emitted_notices, reset_degrade_notices
        _init_repo(self.tmp)
        reset_degrade_notices()
        self.addCleanup(reset_degrade_notices)
        self.assertIsNone(find_mokata_root(self.tmp))
        self.assertEqual([], emitted_notices(),
                         "a repo that was never a mokata repo must stay silent — the instant exit 0")

    def test_a_worktree_of_a_non_mokata_repo_stays_silent(self):
        """The noise case: worktrees are common, mokata repos are not. Only a worktree of a repo
        that visibly HAS `.mokata/` is worth a word."""
        from mokata.degrade import emitted_notices, reset_degrade_notices
        main = os.path.join(self.tmp, "main")
        os.makedirs(main)
        _init_repo(main)
        wt = _add_worktree(main, "feature")
        reset_degrade_notices()
        self.addCleanup(reset_degrade_notices)
        self.assertIsNone(find_mokata_root(wt))
        self.assertEqual([], emitted_notices())

    def test_non_git_project_root_falls_back_to_start(self):
        self.assertEqual(os.path.abspath(self.tmp), find_project_root(self.tmp))

    def test_project_root_finds_the_vcs_root_when_not_a_mokata_repo(self):
        _init_repo(self.tmp)
        sub = os.path.join(self.tmp, "a", "b")
        os.makedirs(sub)
        self.assertEqual(os.path.realpath(self.tmp), os.path.realpath(find_project_root(sub)))


if __name__ == "__main__":
    unittest.main()
