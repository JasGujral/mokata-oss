"""O16 — the bed must catch what a DEV TREE catches.

Jas's 0.0.26 checkpoint run in his own tree produced 24 failures and 5 errors that the bed's
mirror clone could not produce, because the bed lacks the two things his tree has:

  1. the INTERNAL trees the mirror never receives — `docs/build/`, `docs/talks/`,
     `docs/launch/`, `docs/marketing/`, `CLAUDE.md` — which are a HISTORY and quote superseded
     numbers, old minimum versions and retired command spellings on purpose; and
  2. a virtualenv INSIDE the checkout (`.venv312/`), whose `site-packages` carries thousands of
     third-party pages with their own version prose.

A product guard that walks either one reds on something no reader will ever see. These tests
build a tree with both and assert the scoping rules hold — so the next regression of this class
fails here, on the bed, instead of on the owner's machine at cut time.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

import _internal_scope
import _deprecation_removal as D
import _support  # noqa: F401  (puts src/ on the path)

from mokata import deprecation, docsync

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")


def _tree():
    """A checkout carrying an internal history AND an in-repo venv."""
    root = tempfile.mkdtemp(prefix="mokata-devtree-")
    os.makedirs(os.path.join(root, "docs", "build"))
    os.makedirs(os.path.join(root, "docs", "talks", "pycon-2026"))
    os.makedirs(os.path.join(root, "scripts"))
    os.makedirs(os.path.join(root, ".venv312", "lib", "python3.12", "site-packages", "attr"))

    def write(rel, text):
        with open(os.path.join(root, *rel.split("/")), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(textwrap.dedent(text).lstrip("\n"))

    write("README.md", "# mokata\n\nRequires Claude Code 9.9.9 or newer.\n")
    write("docs/index.md", "# docs\n\nA product page with no stale claim.\n")
    # the history: every shape that bit the real run
    write("docs/build/84-backlog.md", "As of Claude Code 2.1.296 (read 2026-10-13): …\n")
    write("docs/talks/pycon-2026/90-inventory.md",
          "Run `mokata upgrade`, or `/mokata:init` inside Claude Code.\n")
    write("docs/build/109-ide-plan.md", "The native core is 10x faster than the Python walker.\n")
    write("CLAUDE.md", "# internal\n")
    # the in-repo venv
    write(".venv312/pyvenv.cfg", "home = /usr\n")
    write(".venv312/lib/python3.12/site-packages/attr/notes.md", "removed in 19.2.0\n")
    # the boundary itself, in the one form the controls read
    write("scripts/sync-public.sh", """
        #!/usr/bin/env bash
        rsync -a \\
          --exclude='docs/build/' \\
          --exclude='docs/talks/' \\
          --exclude='docs/launch/' \\
          --exclude='docs/marketing/' \\
          --exclude='CLAUDE.md' \\
          "$SRC/" "$DST/"
        INTERNAL_PATHS=(
          docs/build docs/launch docs/marketing docs/talks CLAUDE.md
        )
    """)
    return root


class TheShippedBoundaryIsDerived(unittest.TestCase):

    def setUp(self):
        self.root = _tree()

    def test_the_internal_history_is_not_part_of_the_shipped_tree(self):
        for rel in ("docs/build/84-backlog.md", "docs/talks/pycon-2026/90-inventory.md",
                    "CLAUDE.md"):
            with self.subTest(rel):
                full = os.path.join(self.root, *rel.split("/"))
                self.assertFalse(
                    _internal_scope.is_shipped(self.root, full),
                    "%s is excluded by sync-public.sh, so no product guard may grade it" % rel)

    def test_a_product_page_IS_part_of_the_shipped_tree(self):
        """The control: a scope that excludes everything would also be green."""
        for rel in ("README.md", "docs/index.md"):
            with self.subTest(rel):
                full = os.path.join(self.root, *rel.split("/"))
                self.assertTrue(_internal_scope.is_shipped(self.root, full))

    def test_a_tree_with_no_sync_script_ships_everything(self):
        """On the public mirror the script is absent — there, every page present is a page a
        reader meets, and a guard must grade all of them."""
        os.remove(os.path.join(self.root, "scripts", "sync-public.sh"))
        self.assertEqual(frozenset(), _internal_scope.internal_prefixes(self.root))
        history = os.path.join(self.root, "docs", "build", "84-backlog.md")
        self.assertTrue(_internal_scope.is_shipped(self.root, history))

    def test_docsync_sweeps_the_product_docs_and_not_the_history(self):
        swept = {_support.posix_rel(p, self.root) for p in docsync.find_docs(self.root)}
        self.assertIn("docs/index.md", swept)
        self.assertIn("README.md", swept)
        self.assertNotIn("docs/talks/pycon-2026/90-inventory.md", swept)
        self.assertNotIn("docs/build/84-backlog.md", swept)


class TheRepoWalksSkipAVirtualenv(unittest.TestCase):

    def setUp(self):
        self.root = _tree()

    def test_the_release_claim_corpus_skips_site_packages(self):
        corpus = D.release_claim_corpus(self.root)
        self.assertNotIn(".venv312/lib/python3.12/site-packages/attr/notes.md", corpus)
        self.assertEqual((), D.removal_drift(corpus, deprecation.REMOVAL_RELEASE),
                         "a third-party page inside an in-repo venv is not a mokata claim")

    def test_the_corpus_still_reads_the_real_pages(self):
        """The control: the walk is scoped, not switched off."""
        self.assertIn("docs/index.md", D.release_claim_corpus(self.root))

    def test_the_venv_is_found_by_its_marker_not_its_name(self):
        """`.venv312` is not in any name list — `pyvenv.cfg` is what makes it an environment."""
        from mokata import repo_walk
        venv = os.path.join(self.root, ".venv312")
        self.assertTrue(repo_walk.is_python_env(venv))
        renamed = os.path.join(self.root, "py312-env")
        os.rename(venv, renamed)
        self.assertTrue(repo_walk.is_python_env(renamed))
        self.assertNotIn("py312-env/lib/python3.12/site-packages/attr/notes.md",
                         D.release_claim_corpus(self.root))


class TheSecurityLintSurvivesAnUncommittedDelete(unittest.TestCase):
    """Jas's tree had a template the patch deleted but the index still tracked. The lint listed
    the INDEX, could not open the missing file, and raised `CannotRun` — a crash on an ordinary
    user action (apply a patch, do not commit yet). It now lists what is on disk."""

    def _lint(self, root):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        try:
            import importlib
            import security_lint
            return importlib.reload(security_lint)
        finally:
            sys.path.pop(0)

    def test_a_tracked_but_deleted_template_is_skipped_not_a_crash(self):
        root = tempfile.mkdtemp(prefix="mokata-lint-")
        tdir = os.path.join(root, "src", "mokata", "templates", "commands")
        os.makedirs(tdir)
        for name in ("stay.md", "gone.md"):
            with open(os.path.join(tdir, name), "w", encoding="utf-8") as fh:
                fh.write("Run `mokata doctor`.\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for argv in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
            subprocess.run(["git", "-C", root, *argv], check=True, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True)
        os.remove(os.path.join(tdir, "gone.md"))           # the uncommitted delete
        lint = self._lint(root)
        findings = lint.lint_shell_args(root)              # must not raise CannotRun
        self.assertEqual([], [f for f in findings if "gone.md" in getattr(f, "path", "")])

    def test_a_real_offender_still_reds(self):
        """The control: the listing is narrowed, not emptied."""
        root = tempfile.mkdtemp(prefix="mokata-lint-bad-")
        tdir = os.path.join(root, "src", "mokata", "templates", "commands")
        os.makedirs(tdir)
        with open(os.path.join(tdir, "bad.md"), "w", encoding="utf-8") as fh:
            fh.write("```bash\nmokata query $ARGUMENTS\n```\n")
        lint = self._lint(root)
        self.assertTrue(lint.lint_shell_args(root),
                        "an unquoted $ARGUMENTS in a shipped template must still be found")


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()
