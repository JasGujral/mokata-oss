"""A nested checkout is not this repo's source — repo-wide sweeps must not count it.

WHERE THIS CAME FROM (observed live, 2026-08-04). `test_pin_drift_all_pins_guarded` went RED
at a clean HEAD for a reason that was not in the diff: a git worktree sat at
`.claude/worktrees/stage-1b-si6-behavioural`, and the pin sweep — which walks the repo root —
descended into it and counted the worktree's copy of every pinned file as an ADDITIONAL,
unguarded pin. The tree was created by Claude Code for a parallel session, not by mokata.

The controlled experiment (run before this file was written, at one HEAD): sweep GREEN with no
worktree → add a throwaway worktree under `.claude/worktrees/` → sweep RED, naming the two
duplicated pin files → remove the worktree → sweep GREEN again. Causation observed, not
inferred.

WHY THE PIN IS PHRASED STRUCTURALLY. The tempting fix is to add `.claude` to the sweep's
skip-list, and it would have cleared that red. It also encodes today's tool: the exposure is
not the NAME `.claude`, it is that a second checkout was placed inside the walk. Any tool that
picks a different directory — `worktrees/`, a scratch clone, a vendored submodule — reopens it,
and the sweep would again be counting somebody else's files as mokata's own.

So the property pinned here is the one that actually holds: **a directory carrying a `.git`
entry is a checkout boundary and is not this repo's source, whichever tool made it and wherever
it sits.** Both on-disk shapes count — `.git` as a DIRECTORY (a clone or submodule) and `.git`
as a FILE (a `gitdir:` pointer, which is what `git worktree add` writes, and the shape that
caused the incident).

SCOPE, deliberately. `tests/_support.iter_repo_files` is the shared walker this pins; the pin
drift sweep is the one caller that was actually exposed, because it is the only sweep in the
suite rooted at the REPO ROOT. Sweeps scoped to a subtree (`src/mokata`, `tests/`, `docs/`)
were left on their own walks — see the stage report — because a nested checkout does not appear
inside them and converting them would be churn for the look of consistency.

Pure/offline apart from `git init`/`git worktree add` in a temp dir; deterministic.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from _support import iter_repo_files

from mokata.session_worktree import worktree_path_for


def _git(*args, cwd):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )


class _TempTree(unittest.TestCase):
    def tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def _repo(self, tmp):
        """A real one-commit checkout carrying a file the sweeps care about."""
        os.makedirs(os.path.join(tmp, "docs", "how-to"), exist_ok=True)
        with open(os.path.join(tmp, "docs", "how-to", "pin.md"), "w", encoding="utf-8") as fh:
            fh.write("uses: JasGujral/mokata-oss/.github/actions/mokata-check@v0.0.17\n")
        _git("init", "-q", cwd=tmp)
        _git("config", "user.email", "t@example.invalid", cwd=tmp)
        _git("config", "user.name", "t", cwd=tmp)
        _git("add", "-A", cwd=tmp)
        _git("commit", "-qm", "seed", cwd=tmp)
        return tmp

    def _rels(self, root):
        return sorted(rel for rel, _ab in iter_repo_files(root))


class TestNestedCheckoutIsNotCounted(_TempTree):
    """The behaviour the incident needed and the suite did not have."""

    def test_a_git_worktree_inside_the_root_does_not_change_the_file_set(self):
        # The EXACT shape that caused the live red: `git worktree add` writes `.git` as a FILE.
        root = self._repo(self.tmp())
        before = self._rels(root)
        _git("worktree", "add", "-q", "--detach",
             os.path.join(".claude", "worktrees", "probe"), "HEAD", cwd=root)
        nested = os.path.join(root, ".claude", "worktrees", "probe")
        self.assertTrue(os.path.isfile(os.path.join(nested, ".git")),
                        "precondition: a worktree's `.git` is a gitfile, not a directory")
        self.assertTrue(os.path.exists(os.path.join(nested, "docs", "how-to", "pin.md")),
                        "precondition: the worktree really does duplicate the pinned file")
        self.assertEqual(self._rels(root), before,
                         "the sweep counted a nested worktree's files as this repo's source")

    def test_a_nested_clone_does_not_change_the_file_set(self):
        # The other shape: `.git` as a DIRECTORY. Same rule, no name in common with the above.
        root = self._repo(self.tmp())
        before = self._rels(root)
        inner = os.path.join(root, "vendor", "somelib")
        os.makedirs(inner)
        self._repo(inner)
        self.assertTrue(os.path.isdir(os.path.join(inner, ".git")),
                        "precondition: a clone's `.git` is a directory")
        self.assertEqual(self._rels(root), before,
                         "the sweep counted a nested clone's files as this repo's source")

    def test_the_boundary_is_structural_not_named_after_one_tool(self):
        # A `.claude`-shaped skip-list would pass the first test and fail this one. A nested
        # checkout in an ORDINARY-looking directory is excluded for the same reason.
        root = self._repo(self.tmp())
        before = self._rels(root)
        inner = os.path.join(root, "scratch")
        os.makedirs(inner)
        self._repo(inner)
        self.assertEqual(self._rels(root), before,
                         "exclusion is keyed on the directory NAME, not on the `.git` boundary")

    def test_the_root_itself_is_never_pruned_for_carrying_git(self):
        # The root of the walk carries `.git` too. Reading the boundary rule naively would
        # prune the whole repo and yield nothing — which every sweep would read as GREEN.
        root = self._repo(self.tmp())
        rels = self._rels(root)
        self.assertIn("docs/how-to/pin.md", rels,
                      "the walk root was pruned as a checkout boundary — sweeps would see an "
                      "empty tree and pass vacuously")

    def test_an_ordinary_directory_named_git_is_still_walked(self):
        # The rule is "contains a `.git` ENTRY", not "is called .git-something".
        root = self._repo(self.tmp())
        os.makedirs(os.path.join(root, "gitignore-docs"))
        with open(os.path.join(root, "gitignore-docs", "a.md"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        self.assertIn("gitignore-docs/a.md", self._rels(root))

    def test_the_repos_own_dot_git_directory_is_not_yielded_as_source(self):
        root = self._repo(self.tmp())
        self.assertEqual([r for r in self._rels(root) if r.startswith(".git/")], [])


class TestIterRepoFilesContract(_TempTree):
    """The helper's own surface, so a caller can rely on it without reading it."""

    def test_paths_are_repo_relative_posix_and_the_abspath_opens(self):
        root = self._repo(self.tmp())
        rels = dict(iter_repo_files(root))
        self.assertIn("docs/how-to/pin.md", rels)
        self.assertNotIn("\\", "".join(rels))
        with open(rels["docs/how-to/pin.md"], encoding="utf-8") as fh:
            self.assertIn("mokata-check@v", fh.read())

    def test_skip_dirs_accepts_a_bare_name_and_a_relative_path(self):
        # The bare-name arm is only DISTINGUISHABLE below the top level: at depth 0 the
        # relative path of `node_modules` is the string `node_modules`, so the path arm alone
        # would skip it and a dropped bare-name arm would survive. Nest it.
        root = self._repo(self.tmp())
        for rel in (os.path.join("editors", "vscode", "node_modules"),
                    os.path.join("docs", "build")):
            os.makedirs(os.path.join(root, rel), exist_ok=True)
            with open(os.path.join(root, rel, "f.md"), "w", encoding="utf-8") as fh:
                fh.write("mokata-check@v9.9.9\n")
        rels = self._rels(root)
        self.assertIn("editors/vscode/node_modules/f.md", rels)   # nothing skipped by default
        self.assertIn("docs/build/f.md", rels)
        kept = sorted(r for r, _ in iter_repo_files(root, skip_dirs={"node_modules",
                                                                    "docs/build"}))
        self.assertNotIn("editors/vscode/node_modules/f.md", kept)   # bare name, nested
        self.assertNotIn("docs/build/f.md", kept)                    # relative path


class TestSessionWorktreesStayOutsideTheRepo(unittest.TestCase):
    """The half of the filed fix that was already true — pinned so it stays true.

    The incident's worktree was Claude Code's, not mokata's: `worktree_path_for` already
    returns a SIBLING of the checkout. That is load-bearing for every repo-wide sweep, and
    nothing was stopping a later edit from moving it inside. Now something is."""

    def test_worktree_path_is_a_sibling_of_the_checkout_not_a_child(self):
        canon = os.path.join(os.sep, "home", "someone", "proj")
        path = worktree_path_for(canon, "feature-x")
        self.assertEqual(os.path.dirname(path), os.path.dirname(canon))
        self.assertNotEqual(path, canon)

    def test_a_session_worktree_is_never_inside_the_repo_root(self):
        for canon in (os.path.join(os.sep, "home", "someone", "proj"),
                      os.path.join(os.sep, "tmp", "a-b.c"),
                      os.path.join(os.sep, "w", "repo")):
            for name in ("feature-x", "stage-1b-si6", "fix"):
                with self.subTest(canon=canon, name=name):
                    path = os.path.abspath(worktree_path_for(canon, name))
                    root = os.path.abspath(canon)
                    self.assertFalse(
                        os.path.commonpath([path, root]) == root,
                        f"session worktree {path} lands INSIDE the repo root {root} — every "
                        "repo-wide sweep would count it as source",
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
