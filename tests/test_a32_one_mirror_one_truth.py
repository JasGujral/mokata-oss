"""0.0.21 stage 02 — `one-mirror-one-truth`. Four rows, one shape: a release that cannot tell
which tree it is in.

  * `TWO-MIRROR-CHECKOUTS-AND-THE-SHELL-FALLBACK-PICKS-THE-STALE-ONE` — two full checkouts of the
    mirror on the release machine, one of them TWO RELEASES BEHIND, whose own `origin/main` equals
    its own HEAD so **it does not report itself as behind**. The cut's idiom was
    `cd ~/dev/mokata-oss 2>/dev/null || cd ~/mokata-oss` with the first path absent, so every
    command ran in the stale one.
  * `MIRROR-CHECKOUT-CARRIES-LOCAL-ONLY-TAGS-AHEAD-OF-THE-RELEASE-LINE` — six tags from a retired
    scheme that sort ABOVE the current release line under `--sort=-v:refname`.
  * `SYNC-CHECKOUT-FALLBACK-MASKS-THE-REAL-FAILURE` — `2>/dev/null ||` discarded the one sentence
    that named the problem and showed the operator the fallback's error instead.
  * `RELEASE-SH-HARDCODES-main` — the mirror's default branch written five times across two
    scripts.

⭐ **THESE RUN THE REAL SCRIPT.** `scripts/verify-mirror.sh` exists as its own file precisely so
each refusal can be driven with a real synthetic offender rather than asserted by reading text — a
gate inside a 900-line script that ends in a tag and a PyPI publish can only be read, and a reader
grades my transcription (§7c). Synthetic repos only; the real mirror is never involved.

Requires `git` and `bash`; skipped without them.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MISSING = [t for t in ("bash", "git") if shutil.which(t) is None]

_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

# `scripts/verify-mirror.sh` and `scripts/release.sh` are INTERNAL — excluded from the public
# mirror by both of `sync-public.sh`'s controls — so every class reading or running them carries
# the DECORATOR guard. `tests/_shipped_reads.py` refuses a `setUpClass` skip (GUARD_SETUPCLASS_SKIP)
# because a skip arranged inside a class is invisible to a static reader.
VERIFY_SH = os.path.join(_REPO, "scripts", "verify-mirror.sh")
RELEASE_SH = os.path.join(_REPO, "scripts", "release.sh")
SYNC_SH = os.path.join(_REPO, "scripts", "sync-public.sh")
_INTERNAL = "scripts/verify-mirror.sh is dev-only, excluded from the public mirror"


class _Mirrors:
    """A synthetic ORIGIN plus checkouts of it — the release machine's situation, in a temp dir."""

    verify_script = None

    def tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return os.path.realpath(d)

    def _git(self, *args, cwd, check=True):
        return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL,
                              env={**os.environ, **_GIT_ENV})

    @staticmethod
    def _write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def origin(self, branch="main"):
        """A bare-ish origin with one commit on `branch`."""
        work = self.tmp()
        self._git("init", "-q", work, cwd=self.tmp())
        self._git("symbolic-ref", "HEAD", "refs/heads/" + branch, cwd=work)
        self._write(os.path.join(work, "README.md"), "mirror\n")
        self._git("add", "-A", cwd=work)
        self._git("commit", "-qm", "release 0.0.17", cwd=work)
        bare = os.path.join(self.tmp(), "origin.git")
        self._git("clone", "-q", "--bare", work, bare, cwd=work)
        return bare

    def checkout(self, origin):
        d = os.path.join(self.tmp(), "mirror")
        self._git("clone", "-q", origin, d, cwd=self.tmp())
        return d

    def advance_origin(self, origin, branch="main", message="release 0.0.19"):
        """Move origin forward WITHOUT the checkout noticing — the measured stale case."""
        staging = os.path.join(self.tmp(), "staging")
        self._git("clone", "-q", origin, staging, cwd=self.tmp())
        self._write(os.path.join(staging, "NEW.md"), message + "\n")
        self._git("add", "-A", cwd=staging)
        self._git("commit", "-qm", message, cwd=staging)
        self._git("push", "-q", "origin", branch, cwd=staging)

    def verify(self, checkout, repo=""):
        argv = [_support.as_posix(self.verify_script), _support.as_posix(checkout)]
        if repo:
            argv.append(repo)
        return subprocess.run(_support.bash_argv(*argv), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, env={**os.environ, **_GIT_ENV})


@unittest.skipUnless(os.path.exists(VERIFY_SH), _INTERNAL)
@unittest.skipIf(_MISSING, f"needs {', '.join(_MISSING)} on PATH")
class TheStaleCheckoutIsCAUGHT(_Mirrors, unittest.TestCase):
    """`TWO-MIRROR-CHECKOUTS-...-PICKS-THE-STALE-ONE`, driven with a real stale checkout.

    ⭐ THE OFFENDER HAS TO BE BUILT THE WAY THE REAL ONE BROKE. A checkout that is behind and
    KNOWS it is behind is easy to catch and is not the defect. The measured one had **its own
    `origin/main` equal to its own HEAD** — it was in-sync with a ref it last fetched seventeen
    days earlier — so the fixture advances origin through a THIRD clone and never fetches into the
    checkout under test. That is why the gate reads `ls-remote` rather than a local ref."""

    verify_script = VERIFY_SH

    def test_a_CURRENT_checkout_passes_and_echoes_the_default_branch(self):
        """THE CONTROL, AND IT COMES FIRST: every refusal below is also true of a gate that
        refuses everything, which would be a release that can never run."""
        co = self.checkout(self.origin())
        proc = self.verify(co)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual("main", proc.stdout.strip(),
                         "it must ECHO the branch, because that is how the caller stops writing "
                         "`main` for the fifth time")

    def test_a_STALE_checkout_is_REPORTED_and_NOT_refused_and_the_row_was_wrong_here(self):
        """🔴 THE ROW'S PRESCRIBED REMEDY WAS WRONG, AND IMPLEMENTING IT LITERALLY BROKE SEVEN
        TESTS. It said *"have `release.sh` assert the public checkout's `origin/main` matches the
        remote before it syncs"*. I did exactly that, and
        `test_b2_release_retry.TestN2TheReleaseBranchIsReusedNeverRecut` went red seven ways.

        ⭐ The reason the row could not have known: `sync-public.sh` gained RETRY-GUARD N2 at a
        LATER stage, and N2 deliberately fast-forwards a stale local default branch, because *a
        retry that gets here has usually already MERGED and the release content is on origin's
        default branch* — without the fast-forward the sync mints a commit for already-published
        content and the retry never converges. **So being BEHIND is the routine retry state, and a
        gate that refuses it refuses every second run of the release.**

        *A fix shape written before the mechanism existed is a hypothesis* — the same lesson stage
        05 recorded about `tests/__init__.py`, one stage over, found the same way: by implementing
        it and watching.

        What is left is sharper than the row's version: refuse what a fast-forward CANNOT fix
        (below), and make the fast-forward's own failure a refusal instead of a soft note. Being
        behind is reported, because it is also how the wrong-checkout mistake looks."""
        origin = self.origin()
        co = self.checkout(origin)
        self.advance_origin(origin)

        # ⭐ The premise, asserted rather than assumed — this is the whole subtlety of the row.
        local = self._git("rev-parse", "refs/heads/main", cwd=co).stdout.strip()
        tracked = self._git("rev-parse", "refs/remotes/origin/main", cwd=co).stdout.strip()
        self.assertEqual(local, tracked,
                         "THE PREMISE: this checkout's own origin/main equals its own HEAD, so "
                         "nothing local can tell it that it is behind. A fixture that failed this "
                         "would be testing an easier defect than the one that happened.")

        proc = self.verify(co)
        self.assertEqual(0, proc.returncode,
                         "behind is the ROUTINE RETRY STATE and must not be refused:\n"
                         + proc.stdout + proc.stderr)
        self.assertEqual("main", proc.stdout.strip(), "and it still answers the branch question")
        self.assertIn("NOT FETCHED", proc.stderr,
                      "⭐ AND THIS IS A STATE OF ITS OWN, found by building the real offender. "
                      "`merge-base --is-ancestor` needs BOTH objects, and a checkout that has not "
                      "fetched for seventeen days does not HAVE origin's tip — so my first draft "
                      "reported DIVERGED, the alarming word, for the routine problem, and a real "
                      "divergence reported the same thing (§7g, inside the gate itself).")
        self.assertIn("wrong one of the two mirror checkouts", proc.stderr,
                      "...and it must point at the row's own hazard, because an unexpected "
                      "'behind' is exactly what being in the stale checkout looks like")

    def test_an_AHEAD_checkout_is_its_own_message_not_the_behind_one(self):
        origin = self.origin()
        co = self.checkout(origin)
        self._write(os.path.join(co, "LOCAL.md"), "uncommitted work\n")
        self._git("add", "-A", cwd=co)
        self._git("commit", "-qm", "local only", cwd=co)
        proc = self.verify(co)
        self.assertEqual(1, proc.returncode)
        self.assertIn("AHEAD of origin", proc.stderr)
        self.assertNotIn("BEHIND", proc.stderr,
                         "§7g: a checkout carrying unpublished commits is not a stale one, and "
                         "telling the operator to fast-forward would lose their work")

    def test_a_REAL_divergence_still_says_DIVERGED(self):
        """⚠ THE FIXTURE HAS TO FETCH, and that is the point of the state above: without the
        remote object present, every disagreement looks like a divergence. A genuine divergence is
        one where the checkout HAS origin's tip and still disagrees with it."""
        origin = self.origin()
        co = self.checkout(origin)
        self.advance_origin(origin)
        self._git("fetch", "-q", "origin", cwd=co)   # the object is now here
        self._write(os.path.join(co, "LOCAL.md"), "different work\n")
        self._git("add", "-A", cwd=co)
        self._git("commit", "-qm", "diverging", cwd=co)
        proc = self.verify(co)
        self.assertEqual(1, proc.returncode)
        self.assertIn("DIVERGED from origin", proc.stderr)
        self.assertNotIn("NOT FETCHED", proc.stderr,
                         "it HAS fetched; saying otherwise would be the same conflation in "
                         "reverse")

    def test_a_BEHIND_checkout_that_HAS_fetched_is_counted_and_still_allowed(self):
        """The third arm, and the only one where a commit count is meaningful."""
        origin = self.origin()
        co = self.checkout(origin)
        self.advance_origin(origin)
        self._git("fetch", "-q", "origin", cwd=co)
        proc = self.verify(co)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("BEHIND origin by 1 commit", proc.stderr)
        self.assertIn("the sync fast-forwards it", proc.stderr,
                      "and it says WHY this is allowed, so the note does not read as a warning "
                      "nobody can act on")

    def test_a_DIRTY_checkout_is_REFUSED_and_this_is_the_masked_cause_ITSELF(self):
        """⭐ `SYNC-CHECKOUT-FALLBACK-MASKS-THE-REAL-FAILURE`'s actual root cause, refused at the
        front instead of discovered three steps later. At the 0.0.19 cut the mirror had STAGED
        changes, `git checkout` failed with *"Your local changes to the following files would be
        overwritten"*, and `2>/dev/null` threw that sentence away."""
        co = self.checkout(self.origin())
        self._write(os.path.join(co, "README.md"), "edited by hand\n")
        self._git("add", "-A", cwd=co)
        proc = self.verify(co)
        self.assertEqual(1, proc.returncode,
                         f"a dirty mirror was accepted:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("has uncommitted changes", proc.stderr)
        self.assertIn("README.md", proc.stderr,
                      "and it NAMES the files, because 'the tree is dirty' sends the operator "
                      "looking and 'README.md is staged' does not")
        self.assertIn("THE CAUSE THAT WAS MASKED", proc.stderr)

    def test_a_path_that_does_not_exist_REFUSES_and_does_not_fall_back(self):
        """⭐ THE IDIOM THAT CAUSED IT. `cd ~/dev/mokata-oss 2>/dev/null || cd ~/mokata-oss` with
        the first path absent is how every command ended up in the stale checkout. A missing path
        must be a refusal, never a different path."""
        proc = self.verify(os.path.join(self.tmp(), "nope"))
        self.assertEqual(1, proc.returncode)
        self.assertIn("is not a directory", proc.stderr)
        self.assertIn("must never fall back", proc.stderr)

    def test_a_directory_that_is_not_a_checkout_REFUSES(self):
        proc = self.verify(self.tmp())
        self.assertEqual(1, proc.returncode)
        self.assertIn("not a git checkout", proc.stderr)

    def test_the_WRONG_REPOSITORY_is_refused_when_a_slug_is_given(self):
        co = self.checkout(self.origin())
        proc = self.verify(co, repo="SomeoneElse/not-mokata")
        self.assertEqual(1, proc.returncode)
        self.assertIn("does not point at SomeoneElse/not-mokata", proc.stderr)

    def test_the_wrong_repo_check_does_NOT_catch_the_stale_one_and_the_file_SAYS_so(self):
        """§7f, and it is a claim about the DESIGN rather than the behaviour: both checkouts on the
        release machine have the SAME origin URL, so the repo check cannot be what catches
        staleness. Two checks, two different offenders — stated in the file so nobody later deletes
        the staleness check as redundant with the cheaper one."""
        origin = self.origin()
        co = self.checkout(origin)
        self.advance_origin(origin)
        self._git("fetch", "-q", "origin", cwd=co)
        self._write(os.path.join(co, "LOCAL.md"), "diverging\n")
        self._git("add", "-A", cwd=co)
        self._git("commit", "-qm", "diverging", cwd=co)
        proc = self.verify(co, repo=os.path.basename(origin))
        self.assertEqual(1, proc.returncode)
        self.assertIn("DIVERGED from origin", proc.stderr,
                      "the repo check passed (same origin) and the reachability check is what "
                      "fired — which is the point: they catch different offenders")
        with io.open(self.verify_script, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("THIS DOES NOT CATCH THE STALE CHECKOUT ON ITS OWN", body)


@unittest.skipUnless(os.path.exists(VERIFY_SH), _INTERNAL)
@unittest.skipIf(_MISSING, f"needs {', '.join(_MISSING)} on PATH")
class LocalOnlyTagsAreRefused(_Mirrors, unittest.TestCase):
    """`MIRROR-CHECKOUT-CARRIES-LOCAL-ONLY-TAGS-AHEAD-OF-THE-RELEASE-LINE`.

    ⚠ The row VERIFIED the hazard inert against current tooling and said so rather than implying
    live breakage. This refuses in order to keep it that way, which is a different claim from
    "something is broken today" — and the ordering assertion below is what makes the hazard
    concrete instead of a count."""

    verify_script = VERIFY_SH

    def test_a_tag_that_exists_nowhere_on_origin_is_refused_and_NAMED(self):
        origin = self.origin()
        co = self.checkout(origin)
        self._git("tag", "v1.2.3", cwd=co)
        proc = self.verify(co)
        self.assertEqual(1, proc.returncode)
        self.assertIn("exist nowhere on origin", proc.stderr)
        self.assertIn("v1.2.3", proc.stderr,
                      "and it must NAME them: 'you have local-only tags' is not actionable, "
                      "'git tag -d v1.2.3' is")
        self.assertIn("git tag -d", proc.stderr)

    def test_the_hazard_is_ORDERING_and_the_fixture_proves_the_ordering(self):
        """⭐ WHAT MAKES THIS WORTH A GATE. Not the count — the SORT. A leftover `v1.2.3` from the
        retired scheme sorts ABOVE `v0.0.19`, so anything deriving 'the latest tag' in this
        directory answers with a scheme abandoned several releases ago."""
        origin = self.origin()
        co = self.checkout(origin)
        self._git("tag", "v0.0.19", cwd=co)
        self._git("tag", "v1.2.3", cwd=co)
        top = self._git("tag", "-l", "--sort=-v:refname", cwd=co).stdout.split()
        self.assertEqual("v1.2.3", top[0],
                         "the premise: the retired scheme outranks the live release line, which "
                         "is why a loaded foot-gun in this directory is worth refusing")

    def test_a_tag_that_IS_on_origin_is_fine(self):
        """THE CONTROL. Real release tags live in this directory; refusing them would refuse
        every release after the first."""
        origin = self.origin()
        co = self.checkout(origin)
        self._git("tag", "v0.0.17", cwd=co)
        self._git("push", "-q", "origin", "v0.0.17", cwd=co)
        proc = self.verify(co)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)


# ⚠ THREE INTERNAL FILES, THREE GUARDS, and this class needs all of them because it reads all of
# them. The first version guarded only on `verify-mirror.sh` and ERRORED in the public mirror with
# `FileNotFoundError: .../scripts/release.sh` — found by
# `test_b1_internal_tests_meet_their_subject`'s mirror run, which is the instrument for exactly
# this: a shipped test that opens an internal-only file is green here and broken there.
@unittest.skipUnless(os.path.exists(VERIFY_SH), _INTERNAL)
@unittest.skipUnless(os.path.exists(RELEASE_SH), "scripts/release.sh is dev-only")
@unittest.skipUnless(os.path.exists(SYNC_SH), "scripts/sync-public.sh is dev-only")
@unittest.skipIf(_MISSING, f"needs {', '.join(_MISSING)} on PATH")
class NothingGuessesTheDefaultBranch(_Mirrors, unittest.TestCase):
    """`RELEASE-SH-HARDCODES-main` — and the test is that the ANSWER comes from the remote."""

    verify_script = VERIFY_SH

    def test_a_mirror_whose_default_is_master_resolves_to_master(self):
        """⭐ THE ASSERTION THE ROW IS ABOUT. A hardcoded `main` passes every test written on a
        `main` repo. The offender is a mirror whose default is something else."""
        co = self.checkout(self.origin(branch="master"))
        proc = self.verify(co)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual("master", proc.stdout.strip(),
                         "the branch is asked of the remote, so a mirror that calls its default "
                         "'master' gets 'master' — the 0.0.18 Scorecard lag was this exact "
                         "mismatch one layer up")

    def test_with_NO_remote_answer_it_REFUSES_rather_than_assuming_main(self):
        """⛔ THE FAIL-CLOSED DIRECTION. `origin/HEAD` unset and no `gh` slug means nothing can
        say what the default is — and 'I do not know' must not resolve to 'main' (§7g). A guessed
        branch name IS this row."""
        co = self.checkout(self.origin())
        self._git("symbolic-ref", "-d", "refs/remotes/origin/HEAD", cwd=co)
        proc = self.verify(co)                       # no repo slug ⇒ no gh path
        self.assertEqual(1, proc.returncode,
                         f"it answered anyway:\n{proc.stdout}\n{proc.stderr}")
        self.assertIn("could not derive", proc.stderr)
        self.assertIn("git remote set-head", proc.stderr, "and it says how to fix it")

    def test_there_is_exactly_ONE_derivation_in_the_whole_release_path(self):
        """§7f, as a count. Five answers to one question is what the row filed; one is the fix, and
        a second one reappearing is the row coming back. `sync-public.sh` may READ the variable and
        may CALL this script — it may not derive the name itself."""
        with io.open(SYNC_SH, encoding="utf-8") as fh:
            sync = fh.read()
        code = [ln for ln in sync.splitlines() if not ln.lstrip().startswith("#")]
        guesses = [ln for ln in code
                   if 'DEFAULT_BRANCH="main"' in ln or 'DEFAULT_BRANCH="master"' in ln]
        self.assertEqual([], guesses,
                         "sync-public.sh is guessing the default branch again:\n%s"
                         % "\n".join(guesses))
        self.assertIn("MOKATA_MIRROR_DEFAULT_BRANCH", sync,
                      "it consumes the one derivation")

    def test_release_sh_does_not_hardcode_the_base_branch_anywhere_it_ACTS(self):
        with io.open(RELEASE_SH, encoding="utf-8") as fh:
            release = fh.read()
        code = [ln for ln in release.splitlines() if not ln.lstrip().startswith("#")]
        offenders = [ln.strip() for ln in code
                     if ("--base main" in ln or "rev-parse main" in ln
                         or "checkout main " in ln or '"main..' in ln)]
        self.assertEqual([], offenders,
                         "the mirror's default branch is hardcoded again in release.sh:\n%s"
                         % "\n".join(offenders))
        # ⚠ THE CALL SITE MOVED AT STAGE 03, one stage later, and these two assertions had to
        # follow it. Stage 03 put the mirror check into the COLLECTED cheap-gate block so a stale
        # checkout is reported in the first seconds rather than after three test preflights, which
        # means the derivation now happens inside `verify_mirror_gate` and crosses a subshell
        # boundary through a file. ⭐ The PROPERTY is unchanged — one derivation, consumed, never
        # guessed — so the assertion tracks the property rather than the line it used to be on.
        self.assertIn('branch="$(scripts/verify-mirror.sh "$PUB_CHECKOUT" "$PUB_REPO")"', release,
                      "and it must come from the one derivation")
        self.assertIn('PUB_DEFAULT_BRANCH="$(cat "$MIRROR_BRANCH_FILE"', release,
                      "...and be consumed from it, not re-derived")

    def test_the_verify_step_runs_BEFORE_the_sync(self):
        """⛔ ORDER IS THE PROPERTY. Verifying after the sync is verifying a tree that has already
        been written to — `sync-public.sh` branches and commits."""
        with io.open(RELEASE_SH, encoding="utf-8") as fh:
            release = fh.read()
        verify_at = release.index('gate "the mirror checkout')
        sync_at = release.index('scripts/sync-public.sh "$PUB_CHECKOUT"')
        self.assertLess(verify_at, sync_at,
                        "the mirror is verified AFTER being written to, which is not a gate")


@unittest.skipUnless(os.path.exists(SYNC_SH), "sync-public.sh is dev-only")
class TheFastForwardFAILURE_IsARefusalAndNotANote(unittest.TestCase):
    """⭐ THE SHARP END OF `TWO-MIRROR-CHECKOUTS`, and it is one line.

    `sync-public.sh` fetched origin's default branch, tried `--ff-only`, and on ANY failure printed

        sync: note — could not fast-forward <branch> from origin; using the local branch as the base.

    ⛔ **That is the whole hazard in one line.** The release branch is then cut from whatever the
    local default branch happens to be, with a NOTE rather than a refusal — so a checkout two
    releases behind produces `release/<ver>` from an older tree and every gate downstream of the
    sync passes on the wrong content.

    ⚠ AND THE NOTE IS WHY THE HAZARD SURVIVED RETRY-GUARD N2. The fast-forward N2 added was the
    right mechanism; adding it as a best-effort with a fallback meant the one case it could not
    handle degraded silently into the case it was protecting against."""

    def _code(self):
        with io.open(SYNC_SH, encoding="utf-8") as fh:
            return [ln for ln in fh.read().splitlines() if not ln.lstrip().startswith("#")]

    def test_the_soft_note_that_carried_on_with_a_stale_base_is_GONE(self):
        offenders = [ln.strip() for ln in self._code()
                     if "using the local branch as the base" in ln
                     and not ln.lstrip().startswith(("echo \"      ", '"'))]
        self.assertEqual([], offenders,
                         "the sync is carrying on from a stale base again:\n%s"
                         % "\n".join(offenders))

    def test_both_halves_REFUSE_and_re_emit_git_s_message(self):
        body = "\n".join(self._code())
        self.assertIn('if ! _FF_ERR="$(git fetch -q origin "$DEFAULT_BRANCH" 2>&1)"; then', body,
                      "the fetch must be captured and its failure refused")
        self.assertIn('if ! _FF_ERR="$(git merge --ff-only -q "origin/${DEFAULT_BRANCH}" 2>&1)"; then',
                      body, "and the fast-forward too")
        self.assertEqual(2, body.count('printf \'        %s\\n\' "$_FF_ERR" >&2'),
                         "each refusal re-emits git's own message, because that message is the "
                         "diagnosis")

    def test_the_two_refusals_do_not_share_a_sentence(self):
        """§7g: 'could not reach origin' and 'origin's branch is not an ancestor of mine' are
        different problems with different fixes."""
        body = "\n".join(self._code())
        self.assertIn("could not fetch", body)
        self.assertIn("could not be fast-forwarded", body)


@unittest.skipUnless(os.path.exists(SYNC_SH), "sync-public.sh is dev-only")
class TheMaskedFallbackIsGone(unittest.TestCase):
    """`SYNC-CHECKOUT-FALLBACK-MASKS-THE-REAL-FAILURE`.

    The masked `||` cost a full wrong diagnosis: a row was filed under the theory that
    `refs/heads/main` was missing, and corrected only when Jas's own diagnostic proved it existed.
    Two independent failures were funnelled into one message, and it was the message for the case
    that was NOT happening."""

    def _code(self, path):
        with io.open(path, encoding="utf-8") as fh:
            return [ln for ln in fh.read().splitlines() if not ln.lstrip().startswith("#")]

    def test_the_checkout_fallback_to_a_SECOND_branch_name_no_longer_exists(self):
        # ⚠ `lstrip().startswith` and not `in`: the first version matched
        # `[ -d "$DEST/.git" ] || { echo "... is not a git checkout"; exit 1; }` — a legitimate
        # guard whose MESSAGE happens to contain the words. The rule is about a `git checkout`
        # COMMAND with a fallback, not about a line that mentions one.
        offenders = [ln.strip() for ln in self._code(SYNC_SH)
                     if ln.lstrip().startswith("git checkout") and "||" in ln]
        self.assertEqual([], offenders,
                         "a `||` that substitutes a DIFFERENT branch for a missing one is the "
                         "defect — not the silence. Offenders:\n%s" % "\n".join(offenders))

    def test_git_s_OWN_message_is_captured_and_re_emitted(self):
        body = "\n".join(self._code(SYNC_SH))
        self.assertIn('_CO_ERR="$(git checkout -q "$DEFAULT_BRANCH" 2>&1)"', body,
                      "git's stderr must be captured, because that message is usually the "
                      "diagnosis — 'your local changes would be overwritten' is the sentence "
                      "`2>/dev/null` threw away")
        self.assertIn('printf \'        %s\\n\' "$_CO_ERR"', body,
                      "...and re-emitted, not merely captured")

    @unittest.skipUnless(os.path.exists(VERIFY_SH), _INTERNAL)
    def test_verify_mirror_never_masks_a_failure_it_then_REPORTS(self):
        """⚠ THE NARROWED INVARIANT, and the narrowing is itself a finding. I first wrote in
        `verify-mirror.sh` that *"no `2>/dev/null` appears in this file"* — and it was false in my
        own file by the time I finished it, seven times over. The claim that matches the row is
        that no `||` substitutes a different command, and that every git call whose failure is
        REPORTED captures `2>&1`. The survivors are all PREDICATES whose exit status is the whole
        answer and which report nothing: `merge-base --is-ancestor`, `rev-list --count`,
        `command -v`, and `cat-file -e` — that last one is literally git's "does this object
        exist" test, where stderr is the absence it is asking about."""
        lines = self._code(VERIFY_SH)
        masked = [ln.strip() for ln in lines
                  # ⚠ a `die` MESSAGE that QUOTES the idiom it replaced is not the idiom. The
                  # refusals deliberately name `'2>/dev/null ||'` so an operator reads what used
                  # to happen; convicting that is the same substring-vs-identifier mistake stage
                  # 05's module-list rule had to avoid (§7g).
                  if "2>/dev/null" in ln and not ln.lstrip().startswith('"')
                  and not any(p in ln for p in ("merge-base --is-ancestor", "rev-list --count",
                                                "command -v", "cat-file -e"))]
        self.assertEqual([], masked,
                         "a reported failure is being masked again:\n%s" % "\n".join(masked))
        body = "\n".join(lines)
        self.assertIn('ORIGIN_URL="$(git remote get-url origin 2>&1)"', body)
        self.assertIn('REMOTE_TIP="$(git ls-remote origin "refs/heads/${DEFAULT_BRANCH}" 2>&1)"',
                      body)


if __name__ == "__main__":
    unittest.main()
