"""Stage 3 — CORPUS-IS-A-FILESYSTEM-WALK-NOT-THE-INDEX: the sweep, and the reader it points at.

⚠ §7i, AND THE REASON THIS FILE IS SHAPED THE WAY IT IS. Once the tree carries no undeclared
repo-anchored walk, a guard that swept the real tree would pass whether or not it worked — the
same hole stage 2 fell into INSIDE its own §7i guard, where M03 survived because no fixture
exercised the branch. So the sweep is graded against PLANTED sites, one per shape, and the two
shapes that must NOT be flagged are planted with the same care as the two that must.

The false positive is the expensive one here. `_shipped_reads` and `test_suite_count_integrity`
have to stay filesystem reads, because `sync-public.sh` rsyncs the WORKING TREE — a sweep that
flagged them would send someone to convert them and reintroduce
`SHIPPED-TEST-READS-INTERNAL-FILE` from the blind side. `test_a_working_tree_question_is_not_an
_offender` is therefore not a nicety; it is the guard on the guard.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _corpus_sweep as cs                                          # noqa: E402
from _support import NotACheckout, iter_tracked_files               # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _Tmp(unittest.TestCase):
    def tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return d

    def repo(self):
        """A real one-commit checkout with a tracked file, an ignored one and an untracked one."""
        d = self.tmp()
        with open(os.path.join(d, "tracked.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        with open(os.path.join(d, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("ignored.json\n")
        _git("init", "-q", cwd=d)
        _git("config", "user.email", "t@example.invalid", cwd=d)
        _git("config", "user.name", "t", cwd=d)
        _git("add", "-A", cwd=d)
        _git("commit", "-qm", "seed", cwd=d)
        with open(os.path.join(d, "ignored.json"), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        with open(os.path.join(d, "untracked.py"), "w", encoding="utf-8") as fh:
            fh.write("y = 2\n")
        return d


# ───────────────────────────────────────────── 1. the planted sites, one per shape

# The defect: the question is about what the repo TRACKS, and the code walks the disk.
_INDEX_QUESTION_THAT_WALKS = '''
import os
ROOT = os.path.dirname(os.path.abspath(__file__))

# CORPUS: THE INDEX
def every_published_pin():
    return [f for _d, _s, fs in os.walk(ROOT) for f in fs]
'''

# Correct: the question is about what rsync SHIPS, and the disk is ground truth.
_WORKING_TREE_QUESTION_THAT_WALKS = '''
import os
ROOT = os.path.dirname(os.path.abspath(__file__))

# CORPUS: THE WORKING TREE
def everything_that_ships():
    return [f for _d, _s, fs in os.walk(ROOT) for f in fs]
'''

# The offender the row is actually about: repo-anchored, and nobody said which tree.
_UNDECLARED = '''
import os
ROOT = os.path.dirname(os.path.abspath(__file__))

def some_sweep():
    return sorted(os.listdir(ROOT))
'''

# Not in the class at all: the test built this tree, so "tracked" has no meaning in it.
_FIXTURE = '''
import os, tempfile

def over_a_tree_i_built():
    d = tempfile.mkdtemp()
    return sorted(os.listdir(d))
'''

# The shape the stage-29 AST call list did not contain (§7j) — a relative existence check,
# resolved against the process CWD.
_RELATIVE_EXISTS = '''
import os
POINT = "src/mokata/gate_hook.py"

def is_backed():
    return os.path.exists(POINT)
'''

# A walk pruned by a hand-maintained allow-list rather than by a derived corpus. Still an
# offender: the list is the thing that rots, and every entry in it was added by someone who
# was bitten once.
_ALLOW_LIST_PRUNE = '''
import os
ROOT = os.path.dirname(os.path.abspath(__file__))
SKIP = {".mokata", "__pycache__", "node_modules", ".venv", "build", "dist"}

def swept():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        out.extend(filenames)
    return out
'''


class TestTheSweepFires(unittest.TestCase):
    """Each shape, graded. The two that must NOT fire matter as much as the two that must."""

    def test_an_index_question_that_walks_is_a_mismatch(self):
        sites = cs.mismatched_sites({"m.py": _INDEX_QUESTION_THAT_WALKS})
        self.assertEqual(len(sites), 1, cs.render(cs.sweep({"m.py": _INDEX_QUESTION_THAT_WALKS})))
        self.assertEqual(sites[0].question, cs.ASKS_INDEX)
        self.assertEqual(sites[0].idiom, cs.WALK)

    def test_a_working_tree_question_is_not_an_offender(self):
        """THE FALSE POSITIVE THAT WOULD BREAK `_shipped_reads`. A walk declared against the
        working tree is correct and must survive the sweep untouched."""
        corpus = {"m.py": _WORKING_TREE_QUESTION_THAT_WALKS}
        self.assertEqual(cs.undeclared_sites(corpus), [])
        self.assertEqual(cs.mismatched_sites(corpus), [])
        self.assertEqual([s.question for s in cs.sweep(corpus)], [cs.ASKS_WORKING_TREE])

    def test_a_repo_anchored_walk_with_no_declaration_is_the_offender(self):
        sites = cs.undeclared_sites({"m.py": _UNDECLARED})
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0].anchor, cs.REPO_ANCHORED)
        self.assertEqual(sites[0].question, cs.UNDECLARED)

    def test_a_fixture_tree_is_not_in_the_class(self):
        """The third bucket, kept visible. Folding these into either real answer would inflate
        whichever number the reader wanted — two thirds of this repo's sites are here."""
        sites = cs.sweep({"m.py": _FIXTURE})
        self.assertEqual([s.question for s in sites], [cs.ASKS_NEITHER])
        self.assertEqual(sites[0].anchor, cs.FIXTURE_ANCHORED)
        self.assertEqual(cs.undeclared_sites({"m.py": _FIXTURE}), [])

    def test_a_relative_existence_check_is_caught(self):
        """The site the 50-site derivation could not contain, because `os.path.exists` was not
        in the call list that produced it."""
        sites = cs.sweep({"m.py": _RELATIVE_EXISTS})
        self.assertEqual(len(sites), 1, cs.render(sites))
        self.assertEqual(sites[0].idiom, cs.RELATIVE_EXISTS)
        self.assertEqual(sites[0].question, cs.ASKS_INDEX)

    def test_an_absolute_existence_check_is_not_flagged(self):
        """The negative half of the same rule: an absolute path answers the same from every
        directory, so it is not a corpus question and must not be reported as one."""
        src = 'import os\nP = "/etc/hosts"\ndef f():\n    return os.path.exists(P)\n'
        self.assertEqual(cs.sweep({"m.py": src}), [])

    def test_an_allow_list_prune_is_still_an_offender(self):
        """Pruning by a hand-maintained set is not a derivation — it is the per-caller
        `skip_dirs` shape the row names, and it must not buy a pass."""
        sites = cs.undeclared_sites({"m.py": _ALLOW_LIST_PRUNE})
        self.assertEqual(len(sites), 1, cs.render(sites))
        self.assertEqual(sites[0].anchor, cs.REPO_ANCHORED)


class TestTheAnchorIsDataFlowNotAName(unittest.TestCase):
    """`ROOT`, `root`, `d` are names and name nothing. What decides the class is the ORIGIN."""

    def _anchor(self, src, line_of):
        import ast
        tree = ast.parse(src)
        assigns = cs._assignments(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and cs._dotted(node.func) == line_of:
                return cs.anchor_of(node.args[0], assigns)
        self.fail("no %s call in the fixture" % line_of)

    def test_a_file_chain_is_repo_anchored_however_long(self):
        src = ('import os\nA = os.path.abspath(__file__)\nB = os.path.dirname(A)\n'
               'C = os.path.join(B, "docs")\nos.walk(C)\n')
        self.assertEqual(self._anchor(src, "os.walk"), cs.REPO_ANCHORED)

    def test_a_mkdtemp_chain_is_fixture_anchored(self):
        src = ('import os, tempfile\nD = tempfile.mkdtemp()\nE = os.path.join(D, "sub")\n'
               'os.walk(E)\n')
        self.assertEqual(self._anchor(src, "os.walk"), cs.FIXTURE_ANCHORED)

    def test_a_name_with_two_origins_is_unresolved_not_a_coin_flip(self):
        """The safe direction. A name assigned from both a fixture and the repo must report
        UNRESOLVED — picking one would be a guess wearing a derivation's clothes."""
        src = ('import os, tempfile\nR = os.path.dirname(__file__)\nR = tempfile.mkdtemp()\n'
               'os.walk(R)\n')
        self.assertEqual(self._anchor(src, "os.walk"), cs.UNRESOLVED)

    def test_a_self_referential_assignment_terminates(self):
        """`path = os.path.join(path, name)` is a real cycle in a scope-blind name graph. The
        resolver must stop rather than recurse — this crashed the sweep on first contact with
        the real tree, and the depth limit is what stops it.

        ⚠ A visited-set guard was written beside the depth limit and then DELETED. Mutant M08
        survived because the depth limit already covered this, and re-measuring confirmed it:
        over all 429 test modules the visited set changed not one verdict. A second code path
        that nothing can grade is where the false greens live (pre-1.0: delete, do not keep)."""
        src = ('import os\npath = "a"\npath = os.path.join(path, "b")\nos.walk(path)\n')
        self.assertEqual(self._anchor(src, "os.walk"), cs.UNRESOLVED)

    def test_the_depth_limit_is_what_terminates_it(self):
        """The guard that survived the deletion, graded directly rather than by implication."""
        import ast
        chain = "a0 = __file__\n" + "".join(
            "a%d = os.path.dirname(a%d)\n" % (i, i - 1) for i in range(1, 40))
        tree = ast.parse("import os\n" + chain)
        assigns = cs._assignments(tree)
        deep = ast.parse("a39").body[0].value
        self.assertEqual(cs.anchor_of(deep, assigns), cs.UNRESOLVED)

    def test_the_syntax_tree_walk_is_not_a_filesystem_call(self):
        """`ast.walk` reads no disk. Counting it would have inflated every number in this
        stage's report, and the module it would have hit hardest is this sweep itself."""
        src = 'import ast\ndef f(tree):\n    return [n for n in ast.walk(tree)]\n'
        self.assertEqual(cs.sweep({"m.py": src}), [])


class TestTheDeclarationIsScoped(unittest.TestCase):
    def test_a_declaration_does_not_leak_into_the_next_definition(self):
        """Otherwise one `CORPUS:` comment near the top of a module would silently absolve
        every walk below it — an allow-list with extra steps."""
        src = ('import os\nROOT = os.path.dirname(__file__)\n\n'
               '# CORPUS: THE WORKING TREE\n'
               'def declared():\n    return os.listdir(ROOT)\n\n'
               'def undeclared():\n    return os.listdir(ROOT)\n')
        by_q = cs.by_question({"m.py": src})
        self.assertEqual(len(by_q[cs.ASKS_WORKING_TREE]), 1)
        self.assertEqual(len(by_q[cs.UNDECLARED]), 1)

    def test_a_declaration_in_a_string_is_not_a_declaration(self):
        """It has to be a comment. A `CORPUS:` inside a docstring or an error message is prose
        about the rule, not an assertion of it."""
        src = ('import os\nROOT = os.path.dirname(__file__)\n'
               'def f():\n    """CORPUS: THE WORKING TREE — talking about it."""\n'
               '    return os.listdir(ROOT)\n')
        self.assertEqual(len(cs.undeclared_sites({"m.py": src})), 1)


class TestTheIndexReader(_Tmp):
    """`iter_tracked_files` — the properties the row calls the principled fix."""

    def test_untracked_and_ignored_files_are_absent_by_construction(self):
        d = self.repo()
        rels = sorted(r for r, _ab in iter_tracked_files(d))
        self.assertEqual(rels, [".gitignore", "tracked.py"])

    def test_a_nested_checkout_is_excluded_without_a_boundary_rule(self):
        """The property `is_checkout_boundary` maintains structurally, obtained for free.
        `git ls-files` never reports another checkout's files."""
        d = self.repo()
        _git("clone", "-q", d, os.path.join(d, "nested"), cwd=d)
        rels = sorted(r for r, _ab in iter_tracked_files(d))
        self.assertEqual(rels, [".gitignore", "tracked.py"])
        self.assertTrue(os.path.isfile(os.path.join(d, "nested", "tracked.py")),
                        "precondition: the nested checkout really does duplicate the file")

    def test_a_linked_worktree_reads_its_own_index(self):
        """`SYNC-PUBLIC-GIT-EXCLUDE-IS-DIRECTORY-ONLY` is the standing reminder that naive
        `.git` handling breaks here — `.git` is a FILE in a linked worktree, not a directory."""
        d = self.repo()
        wt = os.path.join(self.tmp(), "wt")
        _git("worktree", "add", "-q", "--detach", wt, "HEAD", cwd=d)
        self.assertTrue(os.path.isfile(os.path.join(wt, ".git")),
                        "precondition: a linked worktree's .git is a file, not a directory")
        rels = sorted(r for r, _ab in iter_tracked_files(wt))
        self.assertEqual(rels, [".gitignore", "tracked.py"])

    def test_a_non_checkout_REFUSES_rather_than_degrading_to_a_walk(self):
        """The false green this whole stage is about, in one assertion. A silent fallback from
        "ask the index" to "walk the disk" would keep answering, quietly change corpus, and
        never go red."""
        d = self.tmp()
        with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        with self.assertRaises(NotACheckout):
            list(iter_tracked_files(d))

    def test_skip_dirs_matches_the_walker_both_ways(self):
        """A caller swapping one reader for the other must not have to rewrite its exclusions."""
        d = self.repo()
        os.makedirs(os.path.join(d, "docs", "build"))
        with open(os.path.join(d, "docs", "build", "note.md"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        # `docs/` only — a bare `add -A` here would track `untracked.py` too and quietly
        # destroy the fixture's whole point.
        _git("add", "docs", cwd=d)
        _git("commit", "-qm", "docs", cwd=d)
        by_name = sorted(r for r, _ in iter_tracked_files(d, skip_dirs={"build"}))
        by_path = sorted(r for r, _ in iter_tracked_files(d, skip_dirs={"docs/build"}))
        self.assertEqual(by_name, [".gitignore", "tracked.py"])
        self.assertEqual(by_path, [".gitignore", "tracked.py"])

    def test_a_tracked_but_deleted_path_is_dropped(self):
        """The index lists it; the disk does not have it; every caller is about to open it."""
        d = self.repo()
        os.remove(os.path.join(d, "tracked.py"))
        self.assertEqual(sorted(r for r, _ in iter_tracked_files(d)), [".gitignore"])


class TestTheRealTree(unittest.TestCase):
    """The live invariant. These are the assertions that keep a NEW undeclared walk out."""

    @classmethod
    def setUpClass(cls):
        cls.corpus = cs.read_corpus(os.path.join(_REPO, "tests"), recursive=True)

    def test_no_repo_anchored_corpus_read_is_undeclared(self):
        offenders = cs.undeclared_sites(self.corpus)
        self.assertEqual(offenders, [],
                         "repo-anchored corpus reads that do not say which tree they mean:\n"
                         + cs.render(offenders))

    def test_no_declaration_contradicts_the_call_it_makes(self):
        bad = cs.mismatched_sites(self.corpus)
        self.assertEqual(bad, [],
                         "sites whose CORPUS: declaration is not what the code does:\n"
                         + cs.render(bad))

    def test_the_sweep_is_not_vacuous_on_the_real_tree(self):
        """A guard reporting zero offenders over an empty corpus is reporting nothing. Stage 2's
        M03 survivor is why this assertion exists: 15/15 was 14/15 an hour earlier."""
        sites = cs.sweep(self.corpus)
        self.assertGreater(len(self.corpus), 400, "the corpus did not load")
        self.assertGreater(len(sites), 100, "the sweep found almost nothing — it is not running")

    def test_the_populations_are_four_and_none_is_folded(self):
        """§7g. "the test built this tree" and "my caller picks the root" are different facts and
        must not share a representation — folding the second into the first is what this sweep
        did until mutant M02 survived and named the 60 sites it was hiding."""
        by_q = cs.by_question(self.corpus)
        self.assertGreater(len(by_q[cs.ASKS_NEITHER]), 30, "the fixture population collapsed")
        self.assertGreater(len(by_q[cs.ASKS_UNKNOWN]), 30, "caller-rooted sites were folded away")
        self.assertGreater(len(by_q[cs.ASKS_WORKING_TREE]), 20)
        self.assertTrue(by_q[cs.ASKS_INDEX], "no site asks the index — the reader is unused")

    def test_a_caller_rooted_helper_is_not_reported_as_a_fixture(self):
        """The named instance. `_shipped_reads.shipped_test_sources` is the ONE site doc 84
        singles out as must-not-convert, and it was reading as "a fixture, not in the class"."""
        sites = [s for s in cs.sweep(self.corpus)
                 if s.module == "_shipped_reads.py" and s.scope == "shipped_test_sources"]
        self.assertTrue(sites, "the named site is no longer in the corpus")
        self.assertEqual([s.question for s in sites], [cs.ASKS_UNKNOWN] * len(sites))


class TestTheContaminantIsMeasured(unittest.TestCase):
    """TESTS-WRITE-DOT-MOKATA-INTO-THE-REPO — the lifecycle half, pinned as a fact not a hope."""

    def test_the_pin_sweep_no_longer_depends_on_what_a_previous_run_left_behind(self):
        """`test_pin_drift`'s corpus was 11,912 paths of which 11,404 were untracked. Reading
        the index makes that number 0 BY CONSTRUCTION — so the verdict can no longer move
        because somebody ran the suite in this tree yesterday.

        ⚠ The sweep caught THIS site, on its own author, the first time this file ran — the
        declaration below was missing and `test_no_repo_anchored_corpus_read_is_undeclared`
        named it. That is better anti-vacuity evidence than any count: the guard fired on code
        written in the same hour by the person writing the guard."""
        # CORPUS: THE INDEX — the whole assertion is that this corpus equals `git ls-files`.
        import test_pin_drift as pd
        skip = pd.TestPinDriftCoverageSweep.SKIP_DIRS
        rels = [rel for rel, _ab in iter_tracked_files(_REPO, skip_dirs=skip)]
        tracked = set(subprocess.run(["git", "-C", _REPO, "ls-files"],
                                     capture_output=True, text=True).stdout.split("\n"))
        self.assertTrue(rels, "the sweep's corpus is empty")
        self.assertEqual([r for r in rels if r not in tracked], [],
                         "the pin sweep's corpus still contains untracked paths")


if __name__ == "__main__":
    unittest.main()
