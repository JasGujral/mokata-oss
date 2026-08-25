"""B2 — `release.sh` must be able to succeed twice, and reuse is why it does not force.

WHAT WAS WRONG. `release.sh` could not be re-run. It was filed as one defect — the release-branch
push carries no `--force` — and it is SEVEN non-idempotent steps, of which the decisive one is not
the push:

    N1  preflight tag guard      refuses at STEP 0. Any run that reached the tag step made every
                                 later run of the script impossible, so fixing the push alone
                                 leaves a release path that cannot START.
    N2  sync-public.sh           `checkout -B` re-cut the branch each run; the CLOCK (not the
                                 content) minted a new commit, which is what the push then rejected.
    N3  the branch push          rejected non-fast-forward — the reported defect.
    N4  gh pr create             its failure was explained as "a PR may already exist" for a state
                                 that is "there is nothing to open". §7g inside the release script.
    N5  gh pr merge              a bare subshell under `set -e`: "already merged" was fatal.
    N6  the dev annotated tag    `git tag -a` on an existing tag exits 128.
    N7  the mirror tag           the same, and the preflight never looked at this one at all.

WHY THE FIX IS REUSE AND NOT A FORCE FLAG — the argument this whole file exists to keep true.
`--force-with-lease` works here; the lease is evaluable because `checkout -B` never touches
`refs/remotes/origin/<branch>`. It is still the wrong fix. The commit a force discards and the one
it publishes have the IDENTICAL TREE — they differ by a clock reading — so forcing republishes
byte-identical content under a new SHA, and `release.sh` gates the merge on
`wait_for_ci_green ... $(git rev-parse "$BRANCH")`. Every retry would therefore throw away a green
CI result and buy the same one again, in a release path that burned five PR-CI attempts at 0.0.18.

`test_reuse_preserves_the_head_the_ci_verdict_was_recorded_against` is that argument as an
assertion, and `test_no_release_push_carries_a_force_flag` is what stops it being undone.

HOW THIS IS GRADED. Behaviour, by running the REAL scripts against REAL local git remotes — the
shape `test_sync_public_nested_checkout.py` established, because a rule with moving parts (a
remote-tracking ref, a tree comparison, a refusal) pinned textually asserts the code somebody
happened to write rather than the behaviour. The GitHub steps (N4/N5) cannot be exercised offline
and MUST NOT be exercised against the live mirror, so they are graded structurally, by
`tests/_release_retry.py`'s pure functions over a SUPPLIED corpus — fed the exact text that
shipped before this stage, so each rule is proven able to convict (doc 85 §7i).

⚠ WHAT THIS DOES NOT PROVE, declared rather than discovered: that `gh` behaves as the case arms
assume. What is proven is that the script ASKS `gh` for the PR's state instead of inferring a
reason from an exit code — which is the defect. A real end-to-end run is a dated obligation in the
stage report, not a claim made here.

Requires bash and git; skipped without them. Nothing outside temp dirs is touched, and the real
mokata-oss checkout is never involved.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _release_retry import (
    RETRY_GUARDS,
    case_arms,
    code_lines,
    missing_guard_markers,
    no_force_pushes,
    push_invocations,
    shell_function_source,
)

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SYNC_SH = os.path.join(_REPO, "scripts", "sync-public.sh")
RELEASE_SH = os.path.join(_REPO, "scripts", "release.sh")

_MISSING = [t for t in ("bash", "rsync", "git") if shutil.which(t) is None]

_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

VER = "9.9.9"
BRANCH = f"release/{VER}"
TAG = f"v{VER}"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# =================================================================== the rules, on planted offenders
class TestTheMirrorBoundaryIsWhyTheGuardsSkip(unittest.TestCase):
    """★ The companion that stops the guards above from hiding a deletion.

    `release.sh` and `sync-public.sh` are held back from the mirror by the same two controls, so
    on any tree carrying one, the other must be there too. Without this, deleting either would
    silently uncollect three classes and the run would still report OK — "excluded from the
    mirror" and "someone deleted it" would share a green (doc 85 §7g).
    """

    def test_the_two_release_scripts_are_present_or_absent_TOGETHER(self):
        self.assertEqual(
            os.path.exists(RELEASE_SH), os.path.exists(SYNC_SH),
            "exactly one of scripts/release.sh and scripts/sync-public.sh exists. They are "
            "excluded from the mirror by the same two controls, so this is not the boundary — "
            "it is a half-deleted tree, and the guarded classes above would skip in silence.")


class TestTheRulesCanConvict(unittest.TestCase):
    """§7i. Every rule below is fed the text that ACTUALLY SHIPPED before this stage. A rule that
    only ever sees the fixed tree has graded nothing."""

    # The pre-stage text, transcribed. Not paraphrased — these are the lines that were removed.
    OLD_PUSH = '( cd "$PUB_CHECKOUT" && git push -u origin "$BRANCH" )\n'
    OLD_PR = ('( cd "$PUB_CHECKOUT" && gh pr create --base main --head "$BRANCH" \\\n'
              '    --title "Release ${VER}" --body-file "$RELEASE_NOTES" ) \\\n'
              '  || echo "  (a PR for ${BRANCH} may already exist — continuing to the CI gate.)"\n')
    OLD_TAG_PREFLIGHT = ('if git rev-parse "$TAG" >/dev/null 2>&1; then\n'
                         '  echo "tag $TAG already exists locally — bump the version or delete '
                         'the stale tag first."; exit 1\n'
                         'fi\n')

    def test_the_force_detector_convicts_every_spelling(self):
        for spelling in ("git push --force origin x",
                         "git push --force-with-lease -u origin x",
                         "git push --force-with-lease=x:abc origin x",
                         "git push -f origin x",
                         "git push -qf origin x",
                         '( cd "$D" && git push --force-with-lease -u origin "$BRANCH" )'):
            with self.subTest(spelling):
                self.assertTrue(no_force_pushes(spelling),
                                f"a force push spelled {spelling!r} went unconvicted")

    def test_the_force_detector_does_not_convict_the_prose_that_explains_it(self):
        # Both scripts SAY the word, at length, explaining why forcing was rejected. A substring
        # pin would have to choose between convicting that prose and never convicting anything.
        prose = ('# mokata does not force a release branch: --force-with-lease would republish\n'
                 'echo "  mokata does not force a release branch — forcing republishes the same tree" >&2\n')
        self.assertEqual(no_force_pushes(prose), [])
        self.assertEqual(no_force_pushes(self.OLD_PUSH), [],
                         "the pre-stage push carried no force — that is why the retry FAILED, "
                         "and a detector that convicted it would be reading the wrong thing")

    def test_the_case_arm_rule_convicts_a_two_state_guard(self):
        two = 'case "$S" in\n  ABSENT) ;;\n  PRESENT) exit 1 ;;\nesac\n'
        three = 'case "$S" in\n  ABSENT) ;;\n  AT-THIS-COMMIT) echo ok ;;\n  ELSEWHERE) exit 1 ;;\nesac\n'
        self.assertEqual(case_arms(two, "S"), {"ABSENT": False, "PRESENT": True})
        self.assertEqual(case_arms(three, "S"),
                         {"ABSENT": False, "AT-THIS-COMMIT": False, "ELSEWHERE": True})

    def test_the_case_arm_rule_convicts_three_arms_that_are_really_two(self):
        # The collapse this stage removed was not "too few arms" — it was two DIFFERENT facts
        # reaching the same refusal. An arm count alone would pass this.
        fake = ('case "$S" in\n  ABSENT) ;;\n  AT-THIS-COMMIT) echo no ; exit 1 ;;\n'
                '  ELSEWHERE) exit 1 ;;\nesac\n')
        self.assertEqual(case_arms(fake, "S")["AT-THIS-COMMIT"], True,
                         "an arm that exits was reported as a converging arm")

    def test_the_completeness_rule_names_a_guard_that_was_deleted(self):
        surviving = "\n".join(f"# RETRY-GUARD: {n}" for n in RETRY_GUARDS if n != "N5")
        self.assertEqual(missing_guard_markers(surviving), ["N5"])
        self.assertEqual(missing_guard_markers(""), sorted(RETRY_GUARDS))

    def test_the_false_reason_rule_convicts_the_message_that_shipped(self):
        offenders = [ln for _n, ln in code_lines(self.OLD_PR) if "may already exist" in ln]
        self.assertTrue(offenders, "the rule cannot see the message it exists to remove")

    def test_the_old_preflight_really_was_two_states(self):
        # Not an arms question — it had no case at all. The pin is that it refused unconditionally.
        self.assertEqual(case_arms(self.OLD_PREFLIGHT_SUBJECT(), "PREFLIGHT_TAG_STATE"), {})
        self.assertIn("exit 1", self.OLD_TAG_PREFLIGHT)

    def OLD_PREFLIGHT_SUBJECT(self):
        return self.OLD_TAG_PREFLIGHT


# =================================================================== the rules, on the real scripts
# ⚠ THE GUARD IS A CLASS DECORATOR, and it is not decoration. `scripts/release.sh` and
# `scripts/sync-public.sh` are excluded from the public mirror TWICE each, so a SHIPPED test that
# reads them raises on the tree users clone — green here, broken there, and
# `run_public_subset_preflight` refuses the cut. This class shipped without one and
# `test_s28_shipped_reads_guarded` caught it. Both files are named because this class reads both.
@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
class TestTheScriptsObeyTheRules(unittest.TestCase):

    def setUp(self):
        self.release = _read(RELEASE_SH)
        self.sync = _read(SYNC_SH)

    # --- F14: no force, anywhere. The single most important line in this file. ---------------
    def test_no_release_push_carries_a_force_flag(self):
        offenders = no_force_pushes(self.release) + no_force_pushes(self.sync)
        self.assertEqual(offenders, [],
                         "a force push was reintroduced into the release path. Forcing works — the "
                         "lease is evaluable — and it is still wrong: it republishes an identical "
                         "tree under a new SHA and discards the CI result the merge gates on.")
        self.assertTrue(push_invocations(self.release),
                        "no `git push` found at all — the rule graded an empty corpus")

    # --- N1 --------------------------------------------------------------------------------
    def test_n1_the_preflight_tag_guard_has_three_arms_and_only_one_refuses(self):
        arms = case_arms(self.release, "PREFLIGHT_TAG_STATE")
        self.assertEqual(sorted(arms), ["ABSENT", "AT-THIS-COMMIT", "ELSEWHERE"])
        self.assertFalse(arms["ABSENT"])
        self.assertFalse(arms["AT-THIS-COMMIT"],
                         "the retry case refuses at step 0 — that IS the defect, restored")
        self.assertTrue(arms["ELSEWHERE"], "a tag on a different commit must refuse")

    def test_n1_the_preflight_no_longer_refuses_on_the_bare_existence_of_the_tag(self):
        for _n, line in code_lines(self.release):
            self.assertNotIn("already exists locally — bump the version", line)

    # --- N4: the false reason is gone, and the state is ASKED for ---------------------------
    def test_n4_no_live_code_explains_a_failure_as_a_pr_that_may_already_exist(self):
        offenders = [(n, ln) for n, ln in code_lines(self.release)
                     if "may already exist" in ln and ln.lstrip().startswith(("echo", "||"))
                     and "NOT" not in ln]
        self.assertEqual(offenders, [],
                         "the release script again explains 'there is nothing to open' as "
                         "'a PR may already exist' — two facts, one representation (§7g)")

    def test_n4_the_pr_state_is_asked_for_rather_than_inferred(self):
        self.assertRegex(self.release, r"PR_STATE=.*gh pr view .*--json state",
                         "the PR's state must come from gh, not from an exit code")
        arms = case_arms(self.release, "PR_STATE")
        self.assertEqual(sorted(a for a in arms if a),
                         ["*", "CLOSED", "MERGED", "OPEN"],
                         "every PR state the script can meet must be named, including the "
                         "catch-all — an unknown state must refuse, not fall through")
        self.assertTrue(arms["CLOSED"] and arms["*"])
        self.assertFalse(arms["OPEN"] or arms["MERGED"])

    # --- N5 ----------------------------------------------------------------------------------
    def test_n5_the_merge_is_no_longer_a_bare_subshell_under_set_e(self):
        # The script also PRINTS the merge command in its manual-fallback instructions, and that
        # echo is not a call. Select the invocation shape — a subshell that cds and runs it.
        merges = [(n, ln) for n, ln in code_lines(self.release)
                  if re.search(r"^\s*\(\s*cd .*&&\s*gh pr merge\b", ln)]
        self.assertEqual(len(merges), 1, f"expected exactly one merge call, found {merges}")
        _n, line = merges[0]
        self.assertTrue(line.rstrip().endswith("|| {"),
                        "`gh pr merge` must not be a bare subshell: under `set -e` ANY non-zero "
                        "is fatal, including 'the PR is already merged', which is the retry case")

    # --- completeness -------------------------------------------------------------------------
    def test_every_non_idempotent_step_carries_a_named_retry_guard(self):
        missing = missing_guard_markers(self.release, self.sync)
        self.assertEqual(missing, [],
                         "these steps lost the guard that makes them re-runnable: "
                         + ", ".join(f"{n} ({RETRY_GUARDS[n]})" for n in missing))

    # --- the boundary. Asserted before and after, per the brief. --------------------------------
    #
    # ⚠ THE `--exclude` HASH MOVED ONCE, DELIBERATELY, AT 0.0.19 STAGE 12 — and the pin is kept
    # rather than deleted, because "this stage must not touch the boundary" was always the wrong
    # reading of what it protects. It protects the boundary from an INCIDENTAL edit; a stage whose
    # whole subject IS the boundary changes it and updates this line in the same commit, which is
    # the change being deliberate rather than the check being weak.
    #
    #   a803a14ee26e -> 7a5a2237c9b0 : `--exclude='.git/'` lost its trailing slash. rsync reads a
    #   trailing slash as *directories only*, and in a linked worktree `.git` is a regular FILE —
    #   so the one control CLAUDE.md calls the public/OSS boundary excluded nothing at all in the
    #   shape every stage of 0.0.19 was built in. The COUNT is unchanged (31), which is exactly why
    #   a count alone could never have caught it and the hash is here.
    #
    # `INTERNAL_PATHS` is unchanged (27, 295a1a4341c5) and `.git` is deliberately NOT among them:
    # that loop ends in `rm -rf` inside the public checkout, so adding it deletes the mirror's own
    # object store on every sync. Measured — see the comment beside the array.
    INTERNAL_PATHS_SHA1 = "295a1a4341c5"
    EXCLUDE_SHA1 = "7a5a2237c9b0"

    def test_the_public_boundary_controls_are_byte_identical(self):
        block = re.search(r"INTERNAL_PATHS=\(\n(.*?)\n\)\n", self.sync, re.S)
        self.assertIsNotNone(block, "INTERNAL_PATHS is gone — the only public/OSS boundary control")
        names = [t for ln in block.group(1).splitlines()
                 if not ln.strip().startswith("#") for t in ln.split()]
        excludes = re.findall(r"--exclude='([^']*)'", self.sync)
        self.assertEqual(len(names), 27)
        self.assertEqual(len(excludes), 31)
        self.assertEqual(hashlib.sha1(" ".join(names).encode()).hexdigest()[:12],
                         self.INTERNAL_PATHS_SHA1,
                         "INTERNAL_PATHS changed — update this hash in the same commit, or "
                         "you changed the mirror boundary by accident")
        self.assertEqual(hashlib.sha1(" ".join(excludes).encode()).hexdigest()[:12],
                         self.EXCLUDE_SHA1,
                         "the --exclude set changed — update this hash in the same commit, or "
                         "you changed the mirror boundary by accident")


# =================================================================== behaviour, on real git
class _Harness:
    """A synthetic source tree carrying the REAL scripts, mirrored into a REAL checkout with a
    REAL bare origin. `SRC` is `dirname($0)/..`, which is what makes the source synthetic.

    ⚠ A PLAIN MIXIN THAT NAMES NEITHER SCRIPT, and both halves of that were forced by a sweep.

    As a decorated `TestCase` it was collected as a test class carrying zero test methods, so the
    AST census in `test_b1_internal_tests_meet_their_subject` read it as guarded-and-ungraded while
    unittest reported no skip for it — the two derivations disagreed about one class, which is that
    file's entire subject.

    Decorating it as a plain class does not fix that: the census reads DECORATORS FROM SOURCE and
    would still list it, while unittest still collects nothing. And dropping the decorator alone
    does not work either, because `test_s28_shipped_reads_guarded` then charges it with reading an
    internal path unguarded — `shutil.copy2(SYNC_SH, ...)` taints whatever names the constant.

    Both sweeps are satisfied by naming NEITHER constant here. The paths arrive as the class
    attributes `SYNC_SH` / `RELEASE_SH`, set by each concrete class below — which carries the
    guards, does the naming, and is collected, so the census and unittest see the same thing.
    """

    # ⚠ NOT named `SYNC_SH` / `RELEASE_SH`. The sweep matches the CONSTANT'S NAME wherever it
    # appears, `self.` included, so `self.SYNC_SH` re-taints this class exactly as the bare
    # constant did. Different names, set by the guarded classes below — and never defaulted to a
    # real path, or this becomes the unguarded reader again.
    sync_script = None
    release_script = None

    def tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return os.path.realpath(d)

    @staticmethod
    def _write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def git(self, *args, cwd, check=True):
        return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True,
                              text=True, env={**os.environ, **_GIT_ENV})

    def sha(self, ref, cwd):
        return self.git("rev-parse", ref, cwd=cwd).stdout.strip()

    def remote_sha(self, ref, cwd):
        out = self.git("ls-remote", "origin", ref, cwd=cwd).stdout.split()
        return out[0] if out else ""

    def src(self, body="def widget():\n    return 1\n"):
        src = self.tmp()
        os.makedirs(os.path.join(src, "scripts"))
        shutil.copy2(self.sync_script, os.path.join(src, "scripts", "sync-public.sh"))
        self._write(os.path.join(src, "pyproject.toml"), f'version = "{VER}"\n')
        self._write(os.path.join(src, "README.md"), "shippable\n")
        self._write(os.path.join(src, "src", "pkg", "mod.py"), body)
        return src

    def dest(self):
        """A public checkout on `main`, with a bare origin it has already pushed to."""
        bare = self.tmp()
        self.git("init", "-q", "--bare", ".", cwd=bare)
        dest = self.tmp()
        self._write(os.path.join(dest, "seed.txt"), "seed\n")
        self.git("init", "-q", cwd=dest)
        self.git("add", "-A", cwd=dest)
        self.git("commit", "-qm", "seed", cwd=dest)
        self.git("branch", "-M", "main", cwd=dest)
        self.git("remote", "add", "origin", bare, cwd=dest)
        self.git("push", "-q", "-u", "origin", "main", cwd=dest)
        return dest, bare

    _clock = 0

    def sync(self, src, dest, expect_rc=0):
        """Each call commits at a DIFFERENT pinned time, and that is load-bearing.

        The defect being fixed is driven by the clock: re-cutting the branch mints a new commit
        only because the timestamp moved, so two syncs inside the same second produce an IDENTICAL
        sha and a clean push. Leaving the wall clock in the fixture therefore makes "the retry
        converged" indistinguishable from "the retry got lucky" — and it did, intermittently: a
        mutant that disabled the reuse lookup reddened 4 tests on one run and 3 on the next.
        Advancing a pinned clock per call means a re-cut ALWAYS produces a different sha, so a
        reuse that keeps the head is the only way these tests can pass.
        """
        type(self)._clock += 1
        stamp = f"2030-01-01T00:{self._clock // 60:02d}:{self._clock % 60:02d}+0000"
        proc = subprocess.run(
            _support.bash_argv(_support.as_posix(os.path.join(src, "scripts", "sync-public.sh")),
                               _support.as_posix(dest)),
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            env={**os.environ, **_GIT_ENV,
                 "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
        self.assertEqual(proc.returncode, expect_rc,
                         f"sync-public.sh rc={proc.returncode}\n--- stdout ---\n{proc.stdout}"
                         f"\n--- stderr ---\n{proc.stderr}")
        return proc

    def publish(self, src, dest):
        """A first release, taken as far as the branch push — where the 0.0.18 cut died."""
        self.sync(src, dest)
        self.git("push", "-q", "-u", "origin", BRANCH, cwd=dest)
        return self.sha(BRANCH, cwd=dest)


@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
@unittest.skipIf(_MISSING, f"needs {', '.join(_MISSING)} on PATH")
class TestN2TheReleaseBranchIsReusedNeverRecut(_Harness, unittest.TestCase):
    sync_script, release_script = SYNC_SH, RELEASE_SH

    def test_an_absent_remote_branch_is_created_from_the_default_branch(self):
        src, (dest, _bare) = self.src(), self.dest()
        proc = self.sync(src, dest)
        self.assertIn("Committed to", proc.stdout)
        self.assertNotIn("REUSING", proc.stdout, "there was nothing to reuse")
        self.assertTrue(os.path.exists(os.path.join(dest, "src", "pkg", "mod.py")),
                        "the shippable tree did not arrive — the control for every test below")

    def test_an_identical_tree_reuses_the_published_head_and_mints_no_commit(self):
        src, (dest, _bare) = self.src(), self.dest()
        first = self.publish(src, dest)
        proc = self.sync(src, dest)
        self.assertEqual(self.sha(BRANCH, cwd=dest), first,
                         "the retry minted a NEW commit for a byte-identical tree — this is the "
                         "clock, and it is what made the push non-fast-forward")
        self.assertIn("REUSING", proc.stdout)

    def test_reuse_preserves_the_head_the_ci_verdict_was_recorded_against(self):
        """⛔ THE ARGUMENT AGAINST FORCING, AS AN ASSERTION.

        `release.sh` gates the merge on `wait_for_ci_green <repo> $(git rev-parse "$BRANCH")`.
        A verdict is recorded against a SHA. A force-push republishes the identical tree under a
        NEW sha, so the verdict no longer applies and the retry buys a fresh CI cycle for content
        that has not changed — in a release path that burned five CI attempts at 0.0.18.
        """
        src, (dest, _bare) = self.src(), self.dest()
        graded = self.publish(src, dest)
        ci_green = {graded}                       # exactly what wait_for_ci_green reads: sha -> verdict
        self.sync(src, dest)
        head_to_be_merged = self.sha(BRANCH, cwd=dest)
        self.assertIn(head_to_be_merged, ci_green,
                      "the retry's merge candidate has no CI verdict, so the release must re-buy "
                      "one for a tree that did not change")
        # And the tree really is identical — the premise, not an assumption.
        self.assertEqual(self.git("rev-parse", f"{head_to_be_merged}^{{tree}}", cwd=dest).stdout,
                         self.git("rev-parse", f"{graded}^{{tree}}", cwd=dest).stdout)

    def test_the_push_after_a_reuse_is_a_no_op_needing_no_force(self):
        src, (dest, _bare) = self.src(), self.dest()
        self.publish(src, dest)
        self.sync(src, dest)
        proc = self.git("push", "-u", "origin", BRANCH, cwd=dest)
        self.assertIn("up-to-date", (proc.stdout + proc.stderr).lower(),
                      "the retry's push was not a no-op, so something still needs forcing")

    def test_a_different_tree_refuses_and_leaves_the_published_branch_untouched(self):
        src, (dest, _bare) = self.src(), self.dest()
        published = self.publish(src, dest)
        # A DIFFERENT LENGTH, deliberately: rsync's quick check is size+mtime, so a same-size
        # edit made inside the same second is invisible to it and the fixture would not diverge.
        self._write(os.path.join(src, "src", "pkg", "mod.py"),
                    "def widget():\n    return 2   # changed after the branch was published\n")
        proc = self.sync(src, dest, expect_rc=1)
        self.assertIn("REFUSING", proc.stderr)
        self.assertIn(BRANCH, proc.stderr, "the refusal must name the branch")
        self.assertIn("push origin --delete", proc.stderr, "the refusal must name the remedy")
        self.assertEqual(self.remote_sha(f"refs/heads/{BRANCH}", cwd=dest), published,
                         "a divergence was published over the head CI had already graded")

    def test_the_three_states_do_not_share_a_message(self):
        """§7g. 'there is no release to cut', 'the retry converged' and 'the content diverged'
        are three facts. Before this stage the first two were one message and the third did not
        exist."""
        src, (dest, _bare) = self.src(), self.dest()
        fresh = self.sync(src, dest).stdout
        self.publish(src, dest)
        reused = self.sync(src, dest).stdout
        self._write(os.path.join(src, "src", "pkg", "mod.py"),
                    "def widget():\n    return 3   # a third, longer body (see the rsync note above)\n")
        diverged = self.sync(src, dest, expect_rc=1).stderr
        self.assertNotIn("REUSED", fresh)
        self.assertIn("REUSED", reused)
        self.assertNotIn("already in sync", reused,
                         "the converged retry reports the same sentence as 'nothing to release'")
        self.assertIn("REFUSING", diverged)

    def test_a_stale_local_default_branch_is_fast_forwarded_so_the_retry_converges(self):
        """The post-merge retry. `gh pr merge --delete-branch` removes the branch, so the sync
        re-cuts from the default branch — and if that were stale it would mint a commit for
        content already published, and the retry would not converge."""
        src, (dest, bare) = self.src(), self.dest()
        self.publish(src, dest)
        # [simulated gh pr merge --squash --delete-branch] — git performs the squash; the point of
        # the fixture is the STALENESS it leaves behind, which is real.
        self.git("checkout", "-q", "main", cwd=dest)
        self.git("merge", "-q", "--squash", BRANCH, cwd=dest)
        self.git("commit", "-qm", f"Release {VER} (#1)", cwd=dest)
        self.git("push", "-q", "origin", "main", cwd=dest)
        self.git("push", "-q", "origin", "--delete", BRANCH, cwd=dest)
        merged = self.sha("main", cwd=dest)
        # Now make the LOCAL default branch stale, exactly as a second checkout would be.
        other = self.tmp()
        self.git("clone", "-q", bare, other, cwd=self.tmp())
        self.git("reset", "-q", "--hard", "HEAD~1", cwd=other)
        self.assertNotEqual(self.sha("main", cwd=other), merged, "the fixture is not stale")
        shutil.copy2(self.sync_script, os.path.join(src, "scripts", "sync-public.sh"))
        self.sync(src, other)
        self.assertEqual(self.sha(BRANCH, cwd=other), merged,
                         "the retry did not converge onto the merged commit")
        self.assertEqual(self.sha(BRANCH, cwd=other), self.sha("main", cwd=other),
                         "branch != main, so release.sh would try to open a PR with nothing in it "
                         "— the state that produced the false 'a PR may already exist'")


@unittest.skipUnless(os.path.exists(RELEASE_SH) and os.path.exists(SYNC_SH),
                     "release.sh and sync-public.sh are dev-only, excluded from the public mirror")
@unittest.skipIf(_MISSING, f"needs {', '.join(_MISSING)} on PATH")
class TestN6N7TagsConvergeOrRefuseButNeverMove(_Harness, unittest.TestCase):
    sync_script, release_script = SYNC_SH, RELEASE_SH
    """`ensure_tag`, extracted VERBATIM from release.sh and run against a real repo + remote.
    Extracted rather than sourced: sourcing `release.sh` runs a release."""

    def funcs(self):
        text = _read(self.release_script)
        body = "".join(shell_function_source(text, n) for n in
                       ("tag_commit", "remote_tag_commit", "tag_state", "ensure_tag"))
        self.assertEqual(body.count("\n}\n"), 4, "a tag helper is missing from release.sh")
        path = os.path.join(self.tmp(), "funcs.sh")
        self._write(path, body)
        return path

    def ensure_tag(self, dest, expect_rc=0, when=None):
        """`when` pins the TAGGER TIME, and it is load-bearing rather than tidy.

        `git tag -a` stamps the tagger clock into the object, so two runs inside the same second
        produce the SAME sha — and a test that watched the object sha would then be unable to tell
        converging from re-creating, which is precisely the distinction this stage turns on. A
        mutant that replaced the converge arm with `git tag -f -a` SURVIVED for exactly that
        reason. Pinning the clock to a different value per call makes re-creation observable.
        """
        funcs = self.funcs()
        env = {**os.environ, **_GIT_ENV}
        if when:
            env["GIT_COMMITTER_DATE"] = when       # annotated tags take the tagger time from this
        proc = subprocess.run(
            _support.bash_argv("-c",
                               f"set -euo pipefail; source '{_support.as_posix(funcs)}'; "
                               f"ensure_tag '{_support.as_posix(dest)}' origin '{TAG}' "
                               f"'mokata {VER}' 'mirror'"),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, expect_rc,
                         f"ensure_tag rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}")
        return proc

    def test_tag_state_is_the_only_place_the_three_names_exist(self):
        funcs = self.funcs()
        for have, want, expected in (("", "abc", "ABSENT"),
                                     ("abc", "abc", "AT-THIS-COMMIT"),
                                     ("abc", "def", "ELSEWHERE")):
            with self.subTest(have=have, want=want):
                proc = subprocess.run(
                    _support.bash_argv("-c", f"source '{_support.as_posix(funcs)}'; "
                                             f"tag_state '{have}' '{want}'"),
                    stdin=subprocess.DEVNULL, capture_output=True, text=True)
                self.assertEqual(proc.stdout.strip(), expected)

    def test_an_absent_tag_is_created_and_pushed(self):
        _src, (dest, _bare) = self.src(), self.dest()
        proc = self.ensure_tag(dest)
        self.assertIn("created", proc.stdout)
        self.assertEqual(self.remote_sha(f"refs/tags/{TAG}^{{}}", cwd=dest), self.sha("HEAD", cwd=dest))

    def test_a_tag_already_at_this_commit_converges_without_re_creating_the_object(self):
        _src, (dest, _bare) = self.src(), self.dest()
        self.ensure_tag(dest, when="2030-01-01T00:00:00+0000")
        original = self.sha(f"refs/tags/{TAG}", cwd=dest)
        proc = self.ensure_tag(dest, when="2031-06-02T03:04:05+0000")
        self.assertIn("CONVERGED", proc.stdout)
        self.assertIn("ALREADY PUBLISHED", proc.stdout, "the published tag was re-pushed")
        self.assertEqual(self.sha(f"refs/tags/{TAG}", cwd=dest), original,
                         "the annotated tag OBJECT changed — a re-created tag is a different "
                         "object, and the attestation references the published one")

    def test_a_re_created_annotated_tag_really_is_a_different_object(self):
        """The premise of the converge rule, proven rather than asserted. Without this,
        'CONVERGED' could just as well mean 'we re-created it and nothing noticed'."""
        _src, (dest, _bare) = self.src(), self.dest()
        self.git("tag", "-a", TAG, "-m", f"mokata {VER}", cwd=dest)
        first = self.sha(f"refs/tags/{TAG}", cwd=dest)
        self.git("tag", "-d", TAG, cwd=dest)
        env = {**os.environ, **_GIT_ENV, "GIT_COMMITTER_DATE": "2030-01-01T00:00:00+0000"}
        subprocess.run(["git", "tag", "-a", TAG, "-m", f"mokata {VER}"], cwd=dest, check=True,
                       capture_output=True, env=env)
        self.assertNotEqual(self.sha(f"refs/tags/{TAG}", cwd=dest), first)

    def test_a_local_tag_on_a_different_commit_refuses_by_name(self):
        _src, (dest, _bare) = self.src(), self.dest()
        self.git("tag", "-a", TAG, "-m", "old", cwd=dest)
        elsewhere = self.sha("HEAD", cwd=dest)
        self._write(os.path.join(dest, "later.txt"), "later\n")
        self.git("add", "-A", cwd=dest)
        self.git("commit", "-qm", "later", cwd=dest)
        proc = self.ensure_tag(dest, expect_rc=1)
        self.assertIn("REFUSING", proc.stderr)
        self.assertIn(elsewhere, proc.stderr, "the refusal must name the commit the tag points at")
        self.assertIn("does not move an annotated tag", proc.stderr)

    def test_a_published_tag_on_a_different_commit_refuses_and_is_not_moved(self):
        _src, (dest, _bare) = self.src(), self.dest()
        self.ensure_tag(dest)
        published = self.remote_sha(f"refs/tags/{TAG}^{{}}", cwd=dest)
        self.git("tag", "-d", TAG, cwd=dest)
        self._write(os.path.join(dest, "later.txt"), "later\n")
        self.git("add", "-A", cwd=dest)
        self.git("commit", "-qm", "later", cwd=dest)
        proc = self.ensure_tag(dest, expect_rc=1)
        self.assertIn("attestation-bearing", proc.stderr)
        self.assertEqual(self.remote_sha(f"refs/tags/{TAG}^{{}}", cwd=dest), published,
                         "a published, attestation-bearing tag was moved")


if __name__ == "__main__":
    unittest.main()
