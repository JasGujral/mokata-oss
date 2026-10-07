"""0.0.21 stage 01 · `retry-reads-the-head` — the release guard asks WHICH COMMIT, and CLOSED has two arms.

**WHAT THIS STAGE IS NOT.** `RELEASE-RETRY-IMPOSSIBLE` is closed: `release.sh`'s non-idempotent
steps converge, `tag_state()` returns three states and is consumed as three, the branch push carries
no force deliberately, and both `gh pr create` and `gh pr merge` ask gh what is true instead of
inferring a reason from an exit code. **This finishes a mechanism that mostly works.**

TWO ROWS, AND THE FIRST ONE'S SEVERITY HAD TO BE ESTABLISHED BEFORE IT COULD BE FIXED:

  1. `RELEASE-RETRY-GUARD-READS-PR-STATE-BUT-NOT-ITS-HEAD` — the guard asked gh for `state` alone,
     so `OPEN` said nothing about which commit the PR would merge, and the step-4 CI gate took its
     SHA from the **local branch** instead. ⛔ **Established rather than assumed: they CAN diverge,
     and the window is the CI wait itself.** The push carries no force and aborts on rejection, so
     `origin/<branch>` equals the local branch *at the instant of the push* — but a concurrent
     writer that moves it during the MINUTES CI takes is undetected, and the merge is `--admin`,
     which bypasses branch protection's own required checks. On a retry the old arm did not even
     re-read the head: it said *"reusing it. This is a retry."*
  2. `RELEASE-PR-CLOSED-BY-BRANCH-DELETION-READS-AS-DECLINED` — `CLOSED` had one arm and it accused
     a human (*"Someone declined this release"*), offering a remedy (*"bump the version"*) that is
     wrong for a PR whose branch was deleted out from under it. ⚠ **The row's stated CAUSE looks
     wrong and the fix deliberately does not rest on it:** a successful `gh pr merge --delete-branch`
     leaves the PR `MERGED`, not `CLOSED`. So the fix asks the one question that decides the remedy —
     **is the branch still on the remote?** — which is right however the state was reached.

HOW IT IS GRADED, and this is the stage's own bar rather than the exit criterion's: the decision is
a **pure function over supplied state** (`pr_state`), fed synthetic payloads — OPEN-at-head,
OPEN-diverged, OPEN-with-an-unreadable-head, CLOSED-declined, CLOSED-branch-gone, MERGED, absent,
and a state gh has no rule for. ⛔ **A sweep that both discovers and judges cannot be graded**
(doc 85 §7i), which is why none of this is asserted against a live repository.

🔴 **THE EXIT CRITERION THIS FILE CANNOT DISCHARGE, STATED SO NOBODY READS IT AS DISCHARGED.**
doc 108 §5 criterion 2 is *"`release.sh` completes a retry after a RED CI run without closing the PR
and without a version bump — demonstrated once, on a deliberately-reddened run."* That is a live
push, PR and merge against the real mirror: every one is a credentialed action, and those stay with
Jas. **Criterion 2 remains OPEN against his run.** What is closed here is the guard it will exercise.

Copyright 2026 MoStack. Licensed under the Apache License, Version 2.0.
"""

import io
import os
import re
import subprocess
import unittest

import _support  # noqa: F401  (puts src/ on the path)

import _release_retry as RR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")

#: ⚠ THE GUARD BELOW IS A CLASS DECORATOR, AND IT IS NOT DECORATION. `scripts/release.sh` and
#: `scripts/sync-public.sh` are each excluded from the public mirror TWICE, so a SHIPPED test that
#: reads them RAISES on the tree users clone — green here, broken there, and
#: `run_public_subset_preflight` refuses the cut. This file shipped without one and
#: `test_s28_shipped_reads_guarded` caught it, which is the fourth time that guard has caught this
#: class. `test_b2_release_retry.py:182` is the precedent and the exact form.
#:
#: ⛔ It must be the DECORATOR, never a `setUp`/`setUpClass` skip: that collapses a whole class
#: into ONE skip and drops its tests out of unittest's `Ran N` — the shape `_shipped_reads`
#: refuses by name. And a `skipUnless` trades a loud crash on the mirror for a SILENT skip
#: everywhere, so the positive control lives where it already lives: `test_b1` asserts the dev run
#: skips NOTHING and that the mirror's skip count equals the derived one.
#:
#: ⚠ AND IT IS WRITTEN OUT ON EVERY CLASS RATHER THAN HOISTED INTO ONE ALIAS. A tidier
#: `_DEV_ONLY = unittest.skipUnless(...)` applied as `@_DEV_ONLY` was tried first and
#: `_shipped_reads` graded all five classes `guard=none` — correctly: it reads the AST and a
#: module-level name it cannot resolve is not a guard it can vouch for. The sweep is right to be
#: conservative (it refused a constant interpolated into a failure message for the same reason
#: earlier this release), so the repetition stays and this note is why.

# The seven names, typed ONCE here so the test and the script can be compared rather than agreeing
# with themselves. The script's own `pr_vocabulary` is asserted equal to this below.
VOCABULARY = ("NONE", "OPEN-AT-THIS-COMMIT", "OPEN-ELSEWHERE", "MERGED",
              "CLOSED-DECLINED", "CLOSED-BRANCH-GONE", "UNKNOWN")


def script():
    with io.open(RELEASE_SH, encoding="utf-8") as fh:
        return fh.read()


def _pure_functions(tmpdir):
    """`pr_vocabulary` + `pr_state` extracted from the SHIPPED script into a sourceable file.

    Extracted rather than re-implemented: the thing under test has to be the thing that ships, and
    a copy of the logic in the test file would grade the copy. Same technique `test_b2` uses for
    `tag_state`."""
    text = script()
    out = []
    for name in ("pr_vocabulary", "pr_state"):
        body = RR.shell_function_source(text, name)
        assert body, f"{name} is not in {RELEASE_SH}"
        out.append(body)
    path = os.path.join(tmpdir, "pure.sh")
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return path


@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
class ThePrStateFunctionIsPureAndTotal(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.mkdtemp()
        cls.funcs = _pure_functions(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, *args):
        quoted = " ".join("'%s'" % a for a in args)
        proc = subprocess.run(
            _support.bash_argv("-c", f"source '{_support.as_posix(self.funcs)}'; "
                                     f"pr_state {quoted}"),
            stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def test_the_nine_payloads_that_matter_each_get_their_own_name(self):
        """⭐ The whole stage, in one table. Each row is a FACT the old guard could not express.

        ⛔ **THE NINTH ROW WAS ADDED BY A GREEN SURVIVOR.** Every original row carried a non-empty
        `want`, so deleting the `-n "$2"` guard — mutant `S02` — changed nothing any payload could
        see and the mutant lived. Without that guard, `pr_state OPEN "" ""` compares `"" = ""` and
        answers **OPEN-AT-THIS-COMMIT**: an unreadable head and an unreadable intent agreeing that
        they match, in front of an `--admin` merge. `WANT_SHA` comes from a `git rev-parse` whose
        failure yields an empty string, so the row is reachable, not theoretical."""
        cases = (
            # gh state, head, want,  branch-on-remote      expected
            ("",        "",    "abc", "yes", "NONE"),
            ("OPEN",    "abc", "abc", "yes", "OPEN-AT-THIS-COMMIT"),
            ("OPEN",    "def", "abc", "yes", "OPEN-ELSEWHERE"),
            ("OPEN",    "",    "abc", "yes", "OPEN-ELSEWHERE"),
            ("OPEN",    "",    "",    "yes", "OPEN-ELSEWHERE"),   # ← the S02 survivor's row
            ("MERGED",  "abc", "abc", "no",  "MERGED"),
            ("CLOSED",  "abc", "abc", "yes", "CLOSED-DECLINED"),
            ("CLOSED",  "abc", "abc", "no",  "CLOSED-BRANCH-GONE"),
            ("SOMETHING-NEW", "abc", "abc", "yes", "UNKNOWN"),
        )
        for state, head, want, remote, expected in cases:
            with self.subTest(state=state, head=head, remote=remote):
                self.assertEqual(self.call(state, head, want, remote), expected)

    def test_an_UNREADABLE_head_fails_CLOSED_and_that_direction_is_the_point(self):
        """An empty `headRefOid` means gh could not tell us which commit the PR carries. Reading
        that as "it must be ours" is the fail-OPEN answer, in front of an `--admin` merge."""
        self.assertEqual(self.call("OPEN", "", "abc", "yes"), "OPEN-ELSEWHERE")
        # ⚠ AND WHEN THE INTENT IS ALSO UNREADABLE. Two blanks must not agree with each other —
        # that is the arm mutant `S02` walked through.
        self.assertEqual(self.call("OPEN", "", "", "yes"), "OPEN-ELSEWHERE")

    def test_MERGED_does_not_depend_on_the_branch_still_existing(self):
        """`gh pr merge --delete-branch` removes the branch on every successful run, so a MERGED
        PR normally has no branch. Keying MERGED on the branch would make the commonest retry of
        all unreadable."""
        for remote in ("yes", "no"):
            self.assertEqual(self.call("MERGED", "abc", "abc", remote), "MERGED")

    def test_it_is_PURE_no_gh_no_git_no_network(self):
        """§7i — this is what lets the eight payloads above grade anything. A function that went
        and asked a repository could only be tested against whatever that repository happened to
        say."""
        body = RR.shell_function_source(script(), "pr_state")
        for forbidden in (" gh ", "git ", "curl", "$(", "`"):
            self.assertNotIn(forbidden, body,
                             f"pr_state reaches outside its arguments: {forbidden!r}")


@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
class TheVocabularyIsClosedAndLivesInONEPlace(unittest.TestCase):

    def test_pr_vocabulary_is_exactly_the_seven_names(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        funcs = _pure_functions(tmp)
        proc = subprocess.run(
            _support.bash_argv("-c", f"source '{_support.as_posix(funcs)}'; pr_vocabulary"),
            stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(tuple(proc.stdout.split()), VOCABULARY)

    def test_every_name_the_script_SWITCHES_on_is_in_the_vocabulary(self):
        """`tag_state` returns three states and is consumed as three — the same discipline, one
        level up. A name produced and never handled is a silent fall-through to the `*` arm."""
        arms = RR.case_arms(script(), "PR_STATE")
        self.assertTrue(arms, "no `case \"$PR_STATE\" in` block — the guard is not consumed")
        handled = {a for a in arms if a != "*"}
        self.assertEqual(handled, set(VOCABULARY) - {"UNKNOWN"},
                         f"the consumer and the vocabulary disagree: {sorted(handled)}")

    def test_the_two_OPEN_states_do_NOT_share_an_outcome(self):
        """The collapse this stage exists to undo: a guard can have more arms and still be the
        same two-state guard if the new arm behaves like the old one."""
        arms = RR.case_arms(script(), "PR_STATE")
        self.assertFalse(arms["OPEN-AT-THIS-COMMIT"], "the retry path must NOT exit")
        self.assertTrue(arms["OPEN-ELSEWHERE"], "a diverged PR must stop the release")

    def test_the_two_CLOSED_states_both_refuse_but_with_DIFFERENT_remedies(self):
        """Both are refusals — neither is safe to continue through — but the row is about the
        REMEDY, and a wrong remedy is the harm. `bump the version` must not be offered to someone
        whose version is fine."""
        arms = RR.case_arms(script(), "PR_STATE")
        self.assertTrue(arms["CLOSED-DECLINED"])
        self.assertTrue(arms["CLOSED-BRANCH-GONE"])
        text = script()
        declined = text[text.index("CLOSED-DECLINED)"):text.index("CLOSED-BRANCH-GONE)")]
        gone = text[text.index("CLOSED-BRANCH-GONE)"):text.index("    NONE)")]
        self.assertIn("bump the version", declined)
        self.assertIn("does not need bumping", gone)
        self.assertIn("git push -u origin", gone,
                      "the branch-gone arm must name the remedy that actually fixes it")


@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
class TheCiGateAndTheMergeAgreeOnONECommit(unittest.TestCase):

    def test_the_PR_step_gates_CI_on_the_PR_s_head_and_not_the_local_branch(self):
        """⛔ THE §7g DEFECT AT THE CENTRE OF ROW 1. Two derivations of "the commit that will be
        merged" — one from the PR, one from `git rev-parse` — can disagree, and the `--admin` merge
        means nothing downstream notices."""
        text = script()
        gate = [ln for ln in text.splitlines()
                if "wait_for_ci_green" in ln and "release PR" in ln]
        self.assertEqual(len(gate), 1, f"expected one PR-stage CI gate, found {len(gate)}")
        self.assertIn("$CI_SHA", gate[0],
                      "the PR stage still gates CI on a SHA derived somewhere other than the PR")
        self.assertNotIn("git rev-parse", gate[0])

    def test_the_fallback_for_an_already_MERGED_pr_is_DELIBERATE_and_not_an_empty_string(self):
        """There is no open PR head to read on the MERGED path, and gating on `""` would wait for
        CI on nothing. The fallback is written as a fallback, with its reason."""
        text = script()
        self.assertIn('CI_SHA="${PR_HEAD:-$(cd "$PUB_CHECKOUT" && git rev-parse "$BRANCH")}"', text)

    def test_the_head_is_RE_READ_after_CI_and_BEFORE_the_merge(self):
        """⭐ The part that makes this a closed hole rather than a narrowed one. Everything the
        guard knows is true at the instant it was read, and the CI wait is MINUTES — which is
        exactly the window a concurrent writer moves the branch in."""
        text = script()
        gate_at = text.index('wait_for_ci_green "$PUB_REPO" "$CI_SHA"')
        merge_at = text.index('gh pr merge "$BRANCH" --squash')
        between = text[gate_at:merge_at]
        self.assertIn("read_pr", between, "the head is never re-read before the merge")
        self.assertIn("the release PR moved while CI was running", between)
        self.assertIn("exit 1", between, "a PR that moved mid-flight does not stop the merge")

    def test_the_green_is_attributed_to_the_commit_it_was_EARNED_on(self):
        """⛔ **THE REFUSAL HAS TO NAME THE RIGHT COMMIT, AND THE CAPTURE ORDER IS WHAT DECIDES.**

        The message says *"CI concluded green on ${PRE_MERGE_HEAD}; the PR now reads … at
        ${PR_HEAD}"*. That is only true if `PRE_MERGE_HEAD` was taken BEFORE the re-read — taken
        after, both halves name the commit that just arrived and the message confidently reports
        that nothing moved, at the one moment a human is reading it to decide whether to re-run.

        ⭐ This test exists because the mutant that USED to sit here survived. `C03` deleted
        `|| [ "$PR_HEAD" != "$PRE_MERGE_HEAD" ]` and nothing went red — correctly, because that
        clause was UNREACHABLE: every path into the block leaves `PR_STATE` as
        OPEN-AT-THIS-COMMIT, so `PRE_MERGE_HEAD` *is* `WANT_SHA`, and `pr_state` only answers
        OPEN-AT-THIS-COMMIT when the head equals `WANT_SHA`. The clause is deleted (§7f) and the
        property that is actually load-bearing — the ORDER — is graded here instead. *A surviving
        mutant is a question about the subject, not about the mutant.*"""
        text = script()
        block_at = text.index('if [ "$PR_STATE" != "MERGED" ]; then')
        merge_at = text.index('gh pr merge "$BRANCH" --squash')
        block = text[block_at:merge_at]
        capture = block.index('PRE_MERGE_HEAD="$PR_HEAD"')
        reread = block.index("\n    read_pr")
        self.assertLess(capture, reread,
                        "PRE_MERGE_HEAD is captured AFTER the re-read, so the refusal names the "
                        "commit that just arrived as the one CI was green on")
        self.assertIn("CI concluded green on ${PRE_MERGE_HEAD}", block,
                      "the refusal no longer names the commit the green belongs to")

    def test_the_mid_flight_refusal_rests_on_ONE_condition(self):
        """The other half of the §7f deletion, stated so it cannot quietly come back. One
        condition that can fire, not two where only one ever can."""
        text = script()
        block_at = text.index('if [ "$PR_STATE" != "MERGED" ]; then')
        merge_at = text.index('gh pr merge "$BRANCH" --squash')
        block = text[block_at:merge_at]
        guards = [ln for _n, ln in RR.code_lines(block)
                  if "OPEN-AT-THIS-COMMIT" in ln and ln.strip().startswith("if ")]
        self.assertEqual(1, len(guards), "expected one mid-flight guard: %r" % (guards,))
        self.assertNotIn("PRE_MERGE_HEAD", guards[0],
                         "the unreachable head comparison is back in the guard; it cannot be the "
                         "clause that fires, and its mutant will survive again (§7f)")

    def test_both_fields_come_from_ONE_gh_read(self):
        """Two `gh pr view` calls could observe two different PRs — the same two-sources defect one
        level down."""
        body = RR.shell_function_source(script(), "pr_facts")
        self.assertIn("--json state,headRefOid", body)
        # ⛔ COUNTED OVER CODE LINES, NOT OVER THE WHOLE BODY. `pr_facts`' own comment explains the
        # defect it removed — *"Two `gh pr view` calls could observe two different PRs"* — and
        # counting the raw text convicted that sentence, so this assertion reddened on the prose
        # describing the thing it forbids. `_release_retry.code_lines` exists for exactly this and
        # its docstring says so. Third time this release: a sentence about a command is not a
        # command (stage 06's detector, and the `.pooled` pin before it).
        code = "\n".join(line for _n, line in RR.code_lines(body))
        self.assertEqual(code.count("gh pr view"), 1)
        self.assertIn("gh pr view", body,
                      "the corpus is empty, so the count above grades nothing")
        # ⛔ AND THE PROJECTION HAS TO NAME BOTH FIELDS TOO. `--json state,headRefOid` asks for
        # both; `--jq` decides what comes OUT. Mutant `R02` dropped `headRefOid` from the jq and
        # SURVIVED, because this test was reading the request and never the answer — so the
        # function would have asked for the head and returned only the state, and `PR_HEAD` would
        # be empty on every call. §7g: asking for a fact and emitting it are two things.
        jq = [line for _n, line in RR.code_lines(body) if "--jq" in line]
        self.assertEqual(1, len(jq), "the one jq projection is not findable: %r" % (jq,))
        for field in ("state", "headRefOid"):
            self.assertIn(field, jq[0],
                          "the jq projection drops %r, so the read asks for a field it never "
                          "returns: %s" % (field, jq[0].strip()))

    def test_the_old_single_field_read_is_GONE_from_the_PR_step(self):
        """Pre-1.0: deleted, not left beside its replacement (doc 85 §7d). ⚠ The post-merge
        reconciliation read a few lines further on asks `--json state` ALONE on purpose — it is
        answering *did the merge land*, where there is no head to compare — so this is scoped to
        the PR step rather than asserted over the whole file."""
        text = script()
        step = text[text.index("# --- 3b) open the release PR"):
                    text.index("# --- 4b) merge the PR into main")]
        self.assertNotIn("--json state --jq .state", step,
                         "the single-field read is still in the PR step")


@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
class EveryRefusalNamesARemedyThatEXISTS(unittest.TestCase):
    """`AMEND-STEP-2-IS-UNADVERTISED` is the row for pointing at a flag nobody built. A refusal
    whose fix does not exist is worse than no message: it sends a person looking."""

    def _new_arms(self):
        text = script()
        return text[text.index("OPEN-ELSEWHERE)"):text.index("# --- 4) wait for the PR")]

    def test_no_refusal_invents_a_mokata_subcommand(self):
        from mokata.cli import build_parser
        real = set(build_parser()._subparsers._group_actions[0].choices)
        for match in re.finditer(r"mokata ([a-z][a-z-]+)", self._new_arms()):
            with self.subTest(cmd=match.group(1)):
                self.assertIn(match.group(1), real,
                              f"a refusal names `mokata {match.group(1)}`, which does not exist")

    def test_the_git_remedies_are_real_git_commands(self):
        """Not a spell-check: the verbs are checked against GIT'S OWN command list, so git decides.

        ⛔ **IT ASKED `git <cmd> --help` AND THAT IS A PROPERTY OF THE HOST, NOT OF GIT.**
        `--help` hands off to `man`, so on a box with no man pages or no pager it exits non-zero
        for a perfectly real command — which is what it did in the dev VM, reporting *"`git fetch`
        is not a git command"* about the most ordinary command there is.
        `PROPERTY-PINNED-TO-A-HOST-NOT-A-MECHANISM` is a filed row in this repo and this was a
        fresh instance of it.

        ⭐ `git help -a` is git enumerating itself: no man pages, no pager, exit 0 everywhere. The
        pager env is pinned anyway so a developer's `core.pager` cannot change the answer."""
        env = dict(os.environ, GIT_PAGER="cat", MANPAGER="cat", PAGER="cat")
        listing = subprocess.run(["git", "help", "-a"], capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL, env=env)
        self.assertEqual(listing.returncode, 0, "git could not list its own commands")
        known = set(listing.stdout.split())
        self.assertNotIn("definitelynotagitcommand", known,
                         "every token reads as a git command, so this check discriminates nothing")
        for cmd in ("fetch", "push"):
            with self.subTest(cmd=cmd):
                self.assertIn(cmd, known, f"`git {cmd}` is not in git's own command list")
        arms = self._new_arms()
        self.assertIn("git fetch origin", arms)
        self.assertIn("git push -u origin", arms)

    def test_every_new_refusal_writes_to_STDERR(self):
        """A refusal on stdout is invisible in a piped release log, which is where this script is
        read from."""
        for line in self._new_arms().splitlines():
            if "REFUSING:" in line:
                self.assertIn(">&2", line, f"a refusal goes to stdout: {line.strip()}")

    def test_no_refusal_claims_nothing_happened_when_the_branch_WAS_pushed(self):
        """The branch push is above all of these arms, so "nothing has been pushed" would be
        false. The honest line is about the tag and the merge, which is what the arms say."""
        for line in self._new_arms().splitlines():
            self.assertNotIn("nothing has been pushed", line.lower())


@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
class TheMirrorStatusOfReleaseShIsUnchanged(unittest.TestCase):
    """The brief's explicit warning: `release.sh` is excluded by BOTH controls in
    `sync-public.sh` and named in `CLAUDE.md`'s ships-list, and `test_b3` grades that in both
    directions. This stage edits the file and must not move it."""

    def test_release_sh_is_still_excluded_from_the_public_mirror(self):
        with io.open(os.path.join(ROOT, "scripts", "sync-public.sh"), encoding="utf-8") as fh:
            sync = fh.read()
        self.assertIn("release.sh", sync,
                      "release.sh is no longer named in sync-public.sh's controls")


if __name__ == "__main__":
    unittest.main()
