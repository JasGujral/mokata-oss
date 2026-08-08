"""A nested checkout is not this repo's source — the PRODUCT walkers, not just the sweeps.

WHERE THIS CAME FROM. Stage 20 fixed the nested-checkout defect on the TEST surface: a git
worktree placed inside the repo turned a repo-wide pin sweep red, and the fix was a structural
boundary rule (`is_checkout_boundary`) rather than a `.claude` skip-list. Grounding that stage
turned up the same class one layer down, on the surface a USER sees.

Every walker that feeds the code graph prunes by DOT-PREFIX:

    dirnames[:] = [d for d in dirnames if not d.startswith(".")]

`.claude` happens to start with a dot, which is the ONLY reason the incident did not also
corrupt the knowledge graph. The name-shaped defence works by coincidence. In a user's repo a
checkout that does NOT start with a dot — `vendor/lib`, `worktrees/x`, a submodule-shaped clone —
is walked, and every symbol in it is indexed a SECOND time. That graph feeds impact analysis,
`spec_emit`'s blast radius and both floors, so the duplicate is not cosmetic: "what breaks if I
change this" answers with a file the user does not maintain.

WHAT IS PINNED HERE, and why each half matters:

  * a nested checkout at a NON-DOT path is skipped by every product walker. A `.claude`-shaped
    fixture would prove nothing — it is already skipped, for the wrong reason;
  * the dot-prefix rule SURVIVES. The boundary rule is ADDITIONAL, not a replacement: `.venv`,
    `.mypy_cache` and `.mokata` carry no `.git` entry, and dropping the dot rule to "unify" would
    widen indexing enormously;
  * the skip is DECLARED. A silent skip is the fail-open this release has closed repeatedly — a
    user who vendored a dependency deliberately must be able to read why it is unsearchable;
  * the rule has exactly ONE definition. It began life in `tests/_support.py`; two copies of a
    rule this repo has already been burned by name-drift on would be the same defect in a new
    costume.

Pure/offline apart from `git worktree add` in a temp dir; deterministic.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import ast
import io
import os
import shutil
import subprocess
import tempfile
import unittest
import warnings
from contextlib import redirect_stdout

import _support
from _support import is_checkout_boundary

from mokata import repo_walk
from mokata.detect import Detector
from mokata.engine.acmapper import scan_tests
from mokata.knowledge.anchors import scan_anchors
from mokata.knowledge.ast_backend import AstBackend
from mokata.knowledge.grep_backend import GrepBackend
from mokata.knowledge.index import KnowledgeIndex

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# One module of "source" the fixture plants twice — once as the repo's own, once inside the
# nested checkout. Every walker below has an opinion about it: the index fingerprints it, the
# floors answer `defs widget` from it, the anchor scan reads its `@lat`, and `detect` counts it
# as Python.
_MODULE = (
    "# @lat: widget\n"
    "def widget():\n"
    "    return 1\n"
)

_TEST_MODULE = (
    "def test_widget():\n"
    "    # AC-1\n"
    "    assert widget() == 1\n"
)


class _Fixture(unittest.TestCase):
    """A repo whose ONLY duplicate source is inside a nested checkout at a non-dot path."""

    def tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return os.path.realpath(d)

    @staticmethod
    def _write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _source_tree(self, base):
        self._write(os.path.join(base, "pkg", "mod.py"), _MODULE)
        self._write(os.path.join(base, "t", "test_mod.py"), _TEST_MODULE)

    def repo(self, *, marker="dir", nested="vendor/lib"):
        """A repo root carrying its own source plus a nested CHECKOUT holding a byte-identical
        copy. `marker` picks the on-disk shape of the boundary: a `.git` DIRECTORY (a clone or
        submodule) or a `.git` FILE (the `gitdir:` pointer `git worktree add` writes)."""
        root = self.tmp()
        self._source_tree(root)
        inner = os.path.join(root, *nested.split("/"))
        self._source_tree(inner)
        git = os.path.join(inner, ".git")
        if marker == "dir":
            os.makedirs(git)
            self._write(os.path.join(git, "HEAD"), "ref: refs/heads/main\n")
        else:
            self._write(git, "gitdir: /elsewhere/.git/worktrees/lib\n")
        return root

    def hidden_repo(self):
        """A repo whose duplicate copy sits in an ordinary DOT directory carrying no `.git`.
        The dot rule — not the boundary rule — is what must keep this out."""
        root = self.tmp()
        self._source_tree(root)
        self._source_tree(os.path.join(root, ".venv", "lib"))
        return root


# --------------------------------------------------------------------- the rule has one home
class TestTheRuleIsDefinedOnce(unittest.TestCase):
    def test_the_definition_lives_in_src_and_nowhere_else(self):
        # Parsed, not grepped: a text search for the `def` line matches THIS file (which has to
        # name the symbol to assert about it) and would need an exemption that hides a real
        # second copy living in the same file.
        wanted = repo_walk.is_checkout_boundary.__name__
        hits = []
        for base in ("src", "tests"):
            for rel, ab in _support.iter_repo_files(os.path.join(_REPO, base)):
                if not rel.endswith(".py"):
                    continue
                with open(ab, encoding="utf-8", errors="replace") as fh:
                    source = fh.read()
                # This is the only test that compiles EVERY file in the tree, so it is the only
                # one that re-raises the SyntaxWarnings a few docstrings emit (`\`` inside a
                # non-raw string). They are the tree's, not this pin's; muted so the pin's
                # output stays pristine and a real failure is the only thing on stdout.
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    try:
                        tree = ast.parse(source)
                    except SyntaxError:                      # pragma: no cover
                        continue
                if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and n.name == wanted for n in ast.walk(tree)):
                    hits.append(f"{base}/{rel}")
        self.assertEqual(sorted(hits), ["src/mokata/repo_walk.py"],
                         "the boundary rule is defined in more than one place (or moved) — the "
                         "name-drift shape this stage exists to prevent")

    def test_the_test_helper_consumes_the_src_rule_rather_than_a_copy(self):
        self.assertIs(is_checkout_boundary, repo_walk.is_checkout_boundary)


# --------------------------------------------------------------------- every product walker
class TestKnowledgeIndex(_Fixture):
    def test_a_nested_checkout_is_not_indexed(self):
        for marker in ("dir", "file"):
            with self.subTest(marker=marker):
                root = self.repo(marker=marker)
                built = KnowledgeIndex().build(root)
                self.assertIn(os.path.join("pkg", "mod.py"), built)
                self.assertEqual(
                    [p for p in built if p.startswith("vendor")], [],
                    "the freshness index counted a vendored checkout's files as this repo's "
                    "source — every symbol in it is now double-counted")

    def test_a_dot_directory_is_still_skipped(self):
        built = KnowledgeIndex().build(self.hidden_repo())
        self.assertIn(os.path.join("pkg", "mod.py"), built)
        self.assertEqual([p for p in built if ".venv" in p], [],
                         "the dot-prefix rule was dropped rather than added to")


class TestGrepFloor(_Fixture):
    def _defs(self, root):
        return GrepBackend(root=root).query("defs", "widget").references

    def test_a_nested_checkout_does_not_double_answer_defs(self):
        refs = self._defs(self.repo())
        self.assertEqual([r.path for r in refs], [os.path.join("pkg", "mod.py")],
                         "the lexical floor answered `defs widget` twice — once from a checkout "
                         "the user does not maintain")

    def test_a_dot_directory_is_still_skipped(self):
        refs = self._defs(self.hidden_repo())
        self.assertEqual([r.path for r in refs], [os.path.join("pkg", "mod.py")])


class TestAstFloor(_Fixture):
    def _defs(self, root):
        backend = AstBackend(root=root, grep=GrepBackend(root=root))
        return backend.query("defs", "widget").references

    def test_a_nested_checkout_does_not_double_answer_defs(self):
        refs = self._defs(self.repo())
        self.assertEqual([r.path for r in refs], [os.path.join("pkg", "mod.py")],
                         "the AST floor parsed a vendored checkout into the edge graph — impact "
                         "analysis and blast radius now carry duplicate symbols")

    def test_a_dot_directory_is_still_skipped(self):
        refs = self._defs(self.hidden_repo())
        self.assertEqual([r.path for r in refs], [os.path.join("pkg", "mod.py")])


class TestAnchorScan(_Fixture):
    def test_a_nested_checkout_contributes_no_anchors(self):
        anchors = scan_anchors(self.repo())
        self.assertEqual(sorted(a.path for a in anchors),
                         [os.path.join("pkg", "mod.py")],
                         "`lat-check` read drift anchors out of a vendored checkout")

    def test_a_dot_directory_is_still_skipped(self):
        anchors = scan_anchors(self.hidden_repo())
        self.assertEqual(sorted(a.path for a in anchors), [os.path.join("pkg", "mod.py")])


class TestAcMapper(_Fixture):
    def test_a_nested_checkout_contributes_no_ac_coverage(self):
        refs = scan_tests(self.repo(), ["AC-1"])
        self.assertEqual([r.path for r in refs], [os.path.join("t", "test_mod.py")],
                         "the completeness gate counted a vendored checkout's tests as coverage "
                         "of THIS repo's acceptance criteria")

    def test_a_dot_directory_is_still_skipped(self):
        refs = scan_tests(self.hidden_repo(), ["AC-1"])
        self.assertEqual([r.path for r in refs], [os.path.join("t", "test_mod.py")])


class TestDetector(_Fixture):
    """`_python_files_present` gates the `ast` provider, and its own docstring promises it is
    "the same walk the grep/index backends use". If the backends stop indexing a nested checkout
    and detect does not, detect routes to a floor with nothing to answer from."""

    def _repo_with_python_only_in_the_vendored_checkout(self):
        root = self.tmp()
        self._write(os.path.join(root, "README.md"), "no python here\n")
        inner = os.path.join(root, "vendor", "lib")
        self._write(os.path.join(inner, "mod.py"), _MODULE)
        os.makedirs(os.path.join(inner, ".git"))
        return root

    def test_python_only_inside_a_nested_checkout_does_not_count_as_this_repos_python(self):
        root = self._repo_with_python_only_in_the_vendored_checkout()
        self.assertFalse(
            Detector(root=root)._python_files_present(),
            "detect promised the `ast` provider Python that no backend will index")

    def test_the_repos_own_python_still_counts(self):
        self.assertTrue(Detector(root=self.repo())._python_files_present())

    def test_python_only_inside_a_dot_directory_still_does_not_count(self):
        root = self.tmp()
        self._write(os.path.join(root, "README.md"), "x\n")
        self._write(os.path.join(root, ".venv", "mod.py"), _MODULE)
        self.assertFalse(Detector(root=root)._python_files_present())


# --------------------------------------------------------------------- the real on-disk shape
class TestTheRealWorktreeShape(_Fixture):
    """The synthetic fixtures above write the `.git` marker by hand. This one makes git write it,
    so the pin cannot pass against a shape git does not actually produce."""

    @staticmethod
    def _git(*args, cwd):
        subprocess.run(
            ["git", *args], cwd=cwd, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
                 "GIT_CONFIG_SYSTEM": os.devnull},
        )

    def test_a_real_git_worktree_at_a_non_dot_path_is_not_indexed(self):
        if shutil.which("git") is None:                       # pragma: no cover
            self.skipTest("git not on PATH")
        root = self.tmp()
        self._source_tree(root)
        self._git("init", "-q", cwd=root)
        self._git("config", "user.email", "t@example.invalid", cwd=root)
        self._git("config", "user.name", "t", cwd=root)
        self._git("add", "-A", cwd=root)
        self._git("commit", "-qm", "seed", cwd=root)
        self._git("worktree", "add", "-q", "--detach",
                  os.path.join("worktrees", "probe"), "HEAD", cwd=root)
        nested = os.path.join(root, "worktrees", "probe")
        self.assertTrue(os.path.isfile(os.path.join(nested, ".git")),
                        "precondition: a worktree's `.git` is a gitfile")
        self.assertTrue(os.path.exists(os.path.join(nested, "pkg", "mod.py")),
                        "precondition: the worktree really does duplicate the source")
        built = KnowledgeIndex().build(root)
        self.assertEqual([p for p in built if p.startswith("worktrees")], [])


# --------------------------------------------------------------------- the skip is DECLARED
class TestTheSkipIsDeclared(_Fixture):
    """A silent skip is the fail-open this release keeps closing. A user who vendored a
    dependency on purpose must be able to read WHY it went unsearchable — how many checkouts
    were skipped, and where — on the surface that already reports the index's degrade state."""

    def test_the_index_records_what_it_skipped(self):
        idx = KnowledgeIndex()
        root = self.repo(nested="vendor/lib")
        idx.build(root)
        self.assertEqual(idx.skipped_checkouts, [os.path.join("vendor", "lib")])

    def test_a_clean_repo_records_nothing(self):
        idx = KnowledgeIndex()
        root = self.tmp()
        self._source_tree(root)
        idx.build(root)
        self.assertEqual(idx.skipped_checkouts, [])

    def test_a_checkout_inside_a_dot_directory_is_not_reported(self):
        # The incident's own shape. `.claude` never enters the walk at all — it is skipped as
        # HIDDEN, by the rule that predates this stage — so the boundary rule did not skip it
        # and must not claim credit. The record answers "what did the NEW rule cost you".
        idx = KnowledgeIndex()
        root = self.repo(nested=".claude/worktrees/probe")
        idx.build(root)
        self.assertEqual(idx.skipped_checkouts, [])

    def test_the_record_is_refreshed_by_each_walk_not_accumulated(self):
        idx = KnowledgeIndex()
        root = self.repo()
        idx.build(root)
        idx.build(root)
        self.assertEqual(idx.skipped_checkouts, [os.path.join("vendor", "lib")],
                         "repeated walks accumulated duplicate entries")

    def test_an_abandoned_walk_leaves_no_stale_record(self):
        # The freshness cold-walk stops at a file cap, so a walk CAN end part-way. A record left
        # over from the previous walk would then declare a skip this walk never made — the false
        # evidence, arriving through the very channel added to prevent it.
        idx = KnowledgeIndex()
        idx.build(self.repo())
        self.assertEqual(idx.skipped_checkouts, [os.path.join("vendor", "lib")])
        clean = self.tmp()
        self._source_tree(clean)
        for _ab in idx._iter_files(clean, (".py",)):
            break
        self.assertEqual(idx.skipped_checkouts, [])

    def test_many_skipped_checkouts_say_how_many_are_not_shown(self):
        # A silent truncation reads as "that's all of them". Cap the names, never the count.
        from mokata.knowledge.index import (SKIPPED_CHECKOUT_NAME_CAP,
                                            skipped_checkout_lines)
        many = [f"vendor/lib{i}" for i in range(SKIPPED_CHECKOUT_NAME_CAP + 3)]
        line = skipped_checkout_lines(many)[0]
        self.assertIn(str(len(many)), line)
        self.assertIn("+3 more", line,
                      f"the extra checkouts were dropped without saying so: {line}")

    def test_nothing_skipped_says_nothing(self):
        from mokata.knowledge.index import skipped_checkout_lines
        self.assertEqual(skipped_checkout_lines([]), [],
                         "a repo with no vendored tree gained a line about vendored trees")

    def test_mokata_index_names_the_count_and_the_path(self):
        from mokata.cli import main
        from mokata.init import init_repo

        root = self.repo(nested="vendor/lib")
        init_repo(root=root, profile="minimal", assume_yes=True, out=lambda _m: None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["index", "--path", root])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("1 nested checkout", out,
                      f"`mokata index` did not say how many checkouts it skipped:\n{out}")
        self.assertIn("vendor/lib", out.replace(os.sep, "/"),
                      f"`mokata index` did not say WHERE the skipped checkout is:\n{out}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
