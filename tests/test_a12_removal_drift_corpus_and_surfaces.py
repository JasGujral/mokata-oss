"""THE REMOVAL-DRIFT SWEEP READS THE WHOLE TREE, AND KNOWS WHAT A USER CAN ACTUALLY READ.
(0.0.20 stage 12 — lane E.)

Two rows, and they are the two halves of one instrument (doc 84 says so itself: *"that row is about
what the sweep MATCHES, this one about what it READS, and a sweep is only as good as the weaker of
the two"*).

**`REMOVAL-DRIFT-CORPUS-EXCLUDES-INTERNAL-TREES` — what it reads.** The corpus was an ALLOW-LIST of
directories somebody remembered, and `docs/talks/` was not on it. That directory holds
`client-technical-review` and `pycon-2026`, which stated removal at **0.0.17 since 0.0.15** — the
claim carried to an audience for three releases while the sweep built to catch exactly that read
clean. ⭐ *"Internal" is a statement about the tree, not about the audience.*

**`REMOVAL-DRIFT-KEYS-ON-A-WORD` — what it matches.** `removal_drift` never looked at source at
all, so `cli_commands/migrate.py`'s hand-typed `0.0.17` in `--help` — live, user-visible and wrong —
was invisible, in a file the release had already audited for this class.

WHAT LANDED, AND THE ONE NUMBER THAT DECIDED THE SHAPE
------------------------------------------------------
The corpus is now the WHOLE TREE minus a deny-list whose every entry carries a written reason, and
source is swept by a SECOND instrument that reads the AST instead of the prose. That split is not a
preference; it is what the measurement forced. Over the whole tree the prose matcher finds **108**
drifting mentions: **65 in `docs/build`** (a history that is kept rather than edited, on purpose),
**39 in `tests`** (synthetic version fixtures — the exact 31-false-red class the notice-pin guard's
domain filter was written to remove), **3 in `src`** — and **all three of those are comments.**
⛔ A guard that reds on 107 correct things to catch one wrong one is a guard someone turns off.

⭐ **The deny-list is graded in both directions and is the reviewable artefact:** an entry naming a
path that no longer exists is a rule that grades nothing, and a path excluded without a reason
cannot be reviewed at all. Two of the entries are POINTERS rather than holes — `src` and `tests`
name the instrument that grades them instead, and this file asserts that instrument still exists.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

from __future__ import annotations

import ast
import os
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
import _deprecation_removal as R
import _internal_subject as isub
from mokata import deprecation as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: ⚠ DERIVED, NEVER TYPED — and this file learned that the hard way, from the guard one module
#: over. The planted fixtures below need "a release that is not the removal release", and the
#: obvious way to write one is to type `0.0.17`. `test_no_test_hand_types_the_removal_release`
#: reds on exactly that, in any test file whose vocabulary is deprecation, and it red on the first
#: draft of this one. ⭐ That guard was RIGHT: a fixture pinning a literal release is how five
#: green assertions came to certify the old release at 0.0.18, and a synthetic case is not an
#: exemption from the rule, it is the place the rule is easiest to break.
REMOVAL = D.REMOVAL_RELEASE
_head, _, _tail = REMOVAL.rpartition(".")
OTHER = "%s.%d" % (_head, int(_tail) + 1)   #: a release that is definitionally not the derived one


class TheDenyListIsTheReviewableArtefact(unittest.TestCase):
    """An exclusion nobody can read is indistinguishable from a directory somebody forgot."""

    def test_every_denied_path_still_EXISTS(self):
        """⭐ The clause that keeps the list live. A deny-list entry for a path that is gone
        excludes nothing and quietly certifies a rule that no longer applies to anything.

        ⛔ THREE ANSWERS, NOT TWO, AND THIS SHIPPED TEST LEARNED THAT THE EXPENSIVE WAY. A bare
        `os.path.exists` is a claim about the DEV TREE, and half this deny-list is internal —
        `docs/build`, `docs/talks`, `docs/marketing`, `docs/launch` are excluded from the public
        mirror TWICE. So on the mirror this assertion failed for four paths whose absence is the
        boundary WORKING, which is `SHIPPED-TEST-READS-INTERNAL-FILE` arriving as an EXISTENCE
        assertion rather than as a read — the shape `test_s28_shipped_reads_guarded`'s predicate
        does not see, because nothing is opened.

        The three states are the ones `_internal_subject` already models, and the distinction is
        the whole property: **absent on the mirror is BY DESIGN; absent in a dev checkout is the
        dead rule this test exists to catch.** Collapsing them either way loses one of the two.
        Found 2026-09-13 by running the release's own public-subset preflight — and it had already
        shown itself once, in a container dry run, where it was explained away as an artefact of
        `git archive` skipping gitignored directories. It was the defect both times."""
        for maps in (R.DENIED_FROM_RELEASE_CLAIMS, R.DENIED_FROM_USER_SURFACES):
            for path in maps:
                state = isub.subject_state(ROOT, (path,))
                self.assertIn(
                    state, (isub.GRADED, isub.ABSENT_BY_DESIGN),
                    "%r is excluded from the sweep and does not exist. This tree is not a coherent "
                    "public mirror, so the path is GONE rather than held back — the exclusion now "
                    "grades nothing." % path)

    def test_every_exclusion_carries_a_REASON(self):
        for maps in (R.DENIED_FROM_RELEASE_CLAIMS, R.DENIED_FROM_USER_SURFACES):
            for path, reason in maps.items():
                self.assertGreater(len(reason.strip()), 60,
                                   "%r is excluded with no reviewable reason" % path)

    def test_the_two_POINTER_exclusions_still_point_at_something(self):
        """`src` and `tests` are excluded from the prose sweep because a BETTER instrument grades
        them. ⛔ If that instrument is ever deleted, those entries stop being pointers and become
        holes, and nothing else in the tree would notice."""
        self.assertIn("src", R.DENIED_FROM_RELEASE_CLAIMS)
        self.assertIn("tests", R.DENIED_FROM_RELEASE_CLAIMS)
        self.assertTrue(callable(getattr(R, "user_facing_release_claims", None)),
                        "`src` is excluded because `user_facing_release_claims` grades it, and "
                        "that function is gone — the exclusion is now a hole")
        self.assertTrue(callable(getattr(R, "notice_pins", None)),
                        "`tests` is excluded because `notice_pins` grades it, and that function "
                        "is gone — the exclusion is now a hole")


class TheCorpusIsDerivedFromTheTree(unittest.TestCase):

    def setUp(self):
        self.corpus = R.release_claim_corpus(ROOT)

    def test_the_directory_that_carried_the_defect_is_IN_the_corpus(self):
        """`docs/talks/` is the row's own offender, and it is a regression pin, not a sample.

        ⚠ AND IT IS AN INTERNAL DIRECTORY, so the pin has to say WHICH tree it is talking about.
        On the public mirror `docs/talks/` is absent by design and a corpus that lacks it is
        correct; in a dev checkout its absence from the corpus IS the row, verbatim. Same three
        states as the deny-list assertion above, same reason."""
        if isub.subject_state(ROOT, ("docs/talks",)) == isub.ABSENT_BY_DESIGN:
            self.assertFalse(any(k.startswith("docs/talks/") for k in self.corpus),
                             "this tree is the public mirror, where docs/talks does not exist — "
                             "yet the corpus claims to sweep it, so the corpus is reading "
                             "something that is not there")
            return
        self.assertTrue(any(k.startswith("docs/talks/") for k in self.corpus),
                        "docs/talks is outside the corpus again — that is the row verbatim")

    def test_the_corpus_reaches_past_the_published_docs(self):
        """The old corpus was README + docs/ minus three trees. Whatever else changes, a sweep
        that has shrunk back to the published doc set has lost the row's whole point."""
        for prefix in ("scripts/", ".github/"):
            self.assertTrue(any(k.startswith(prefix) for k in self.corpus),
                            "nothing under %r is swept" % prefix)
        self.assertGreater(len(self.corpus), 100, len(self.corpus))

    def test_nothing_is_excluded_EXCEPT_by_a_declared_rule(self):
        """⭐ The anti-allow-list assertion. Every text-bearing path under a content directory is
        either in the corpus or covered by a written exclusion — there is no third state, which is
        the state the missing deck lived in."""
        # CORPUS: THE WORKING TREE — and it has to be, for the same reason `release_claim_corpus`
        # reads the tree rather than the index: both publishing paths copy the working tree, so an
        # UNTRACKED page under `docs/` really is served, and it is exactly the page most likely to
        # carry a hand-typed release. The index would be blind to it, and this assertion exists to
        # prove nothing is invisible.
        missing = []
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "docs")):
            rel_dir = _support.posix_rel(dirpath, ROOT)
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                if not name.endswith(".md"):
                    continue
                rel = "%s/%s" % (rel_dir, name)
                if rel in self.corpus:
                    continue
                if not R._denied(rel):
                    missing.append(rel)
        self.assertEqual([], missing[:20],
                         "%d doc(s) are in neither the corpus nor the deny-list" % len(missing))

    def test_the_corpus_states_ONE_removal_release_and_it_is_the_derived_one(self):
        self.assertEqual((), R.removal_drift(self.corpus, D.REMOVAL_RELEASE))


class ThePlantedOffenderIsCaught(unittest.TestCase):
    """§7i — the tree being clean grades nothing away. Every case below is a SUPPLIED state."""

    def test_a_page_anywhere_in_the_corpus_reds(self):
        planted = {"docs/talks/pycon-2026/deck.md":
                   "The vault channel was REMOVED in mokata %s." % OTHER}
        self.assertEqual((("docs/talks/pycon-2026/deck.md", 1, OTHER),),
                         R.removal_drift(planted, REMOVAL))

    def test_the_same_page_naming_the_DERIVED_release_is_clean(self):
        planted = {"docs/talks/pycon-2026/deck.md":
                   "The vault channel was REMOVED in mokata %s." % REMOVAL}
        self.assertEqual((), R.removal_drift(planted, REMOVAL))


class TheSurfaceScanReadsTheAstNotTheProse(unittest.TestCase):
    """The second axis. A comment cannot reach a user; a `--help` string can."""

    def _scan(self, body, name="mod.py"):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src", "pkg")
            os.makedirs(src)
            with open(os.path.join(src, name), "w", encoding="utf-8") as fh:
                fh.write(body)
            return R.user_facing_release_claims(tmp, REMOVAL)

    def test_a_release_in_HELP_TEXT_is_an_offender(self):
        """The row's own instance, reproduced: a hand-typed release in `--help`."""
        found = self._scan('import argparse\n'
                           'def f(p):\n'
                           '    p.add_argument("--x", help="removed in %s")\n' % OTHER)
        self.assertEqual(1, len(found), found)
        self.assertEqual(OTHER, found[0][2])

    def test_the_SAME_release_in_a_comment_is_NOT(self):
        found = self._scan('# removed in %s, which is correct history\n' % OTHER +
                           'X = 1\n')
        self.assertEqual((), found)

    def test_the_same_release_in_a_DOCSTRING_is_NOT(self):
        """A docstring reaches a reader of the source, which is the same audience a comment has.
        ⛔ Excluded by construction — read off the AST — never by an allow-list of files."""
        found = self._scan('"""This channel was removed in %s."""\n' % OTHER +
                           'def g():\n'
                           '    """Also removed in %s."""\n' % OTHER)
        self.assertEqual((), found)

    def test_a_release_in_an_ERROR_MESSAGE_is_an_offender(self):
        found = self._scan('def g():\n'
                           '    raise ValueError("that backend was removed in %s")\n' % OTHER)
        self.assertEqual(1, len(found), found)

    def test_the_DERIVED_release_in_help_text_is_clean(self):
        """The control on all four above: a scan that flagged everything would satisfy them."""
        found = self._scan('def f(p):\n'
                           '    p.add_argument("--x", help="removed in %s")\n' % REMOVAL)
        self.assertEqual((), found)

    def test_the_REAL_tree_states_no_release_a_user_can_read_but_the_derived_one(self):
        self.assertEqual((), R.user_facing_release_claims(ROOT, D.REMOVAL_RELEASE))


class TheDeclarationModuleDerivesItsOwnRelease(unittest.TestCase):
    """⭐ FOUND BY A SURVIVING MUTANT, not by reading the code.

    `_stage9_removal_mutants.sh` A05 replaces `REMOVAL_RELEASE = removal_target(...)` with a
    literal. It had never been graded, because the batch aborted on a stale pattern seven mutants
    earlier; once 0.0.20 stage 09 unblocked the batch it ran and **survived**. It survives for the
    reason a literal always does: today's literal equals today's derived value, so no behavioural
    test can tell them apart, and the guard that forbids hand-typed releases elsewhere exempts this
    module precisely because it is where the declaration lives.

    ⛔ So the property has to be structural, and it is the one this whole lane rests on: the module
    that DECLARES the removal must still DERIVE the release rather than restate it. A literal here
    is the `REMOVAL-RELEASE-WAS-PINNED-SIX-TIMES` defect at its source — every other copy in the
    tree reads this name."""

    def _assignment(self, name):
        path = os.path.join(ROOT, "src", "mokata", "deprecation.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            targets = ([node.target] if isinstance(node, ast.AnnAssign)
                       else getattr(node, "targets", []) if isinstance(node, ast.Assign) else [])
            for target in targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        return None

    def test_the_removal_release_is_COMPUTED_not_written_down(self):
        value = self._assignment("REMOVAL_RELEASE")
        self.assertIsNotNone(value, "REMOVAL_RELEASE is no longer assigned at module level")
        self.assertIsInstance(
            value, ast.Call,
            "REMOVAL_RELEASE is assigned %s, not a call — the release the whole tree reads has "
            "become a literal in the one module allowed to hold one" % type(value).__name__)

    def test_and_so_is_the_release_it_was_FILED_in(self):
        """The sibling constant, on the same argument. Two derived names, one declaration."""
        value = self._assignment("REMOVAL_FILED")
        self.assertIsNotNone(value, "REMOVAL_FILED is no longer assigned at module level")
        self.assertIsInstance(value, ast.Call, type(value).__name__)
