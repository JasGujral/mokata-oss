"""B3 — the release TAIL is graded: the two repositories' tag sets must AGREE, or say why not.

0.0.19 stage 08, row B3 (`release-tail-ungraded`). ⚠ The row as first filed claimed step 8's CI
gate could never fire. That was FALSE and doc 104 §3 corrected it before this stage opened: the
push trigger exists and it fired on the 0.0.18 release commit. What actually failed is one step
later and has no gate at all.

WHAT WAS WRONG. The dev annotated tag `v0.0.18` was never created. PyPI carried 0.0.18, the mirror
carried its tag, the GitHub Release carried its signed assets — and the dev repository's tags
stopped at `v0.0.17`. Nothing observed it. The only reason anyone knows is that a plan went
looking, by hand, four weeks later.

⭐ AND IT IS NOT THE ONLY ONE. Deriving the property instead of transcribing the row's example
finds a SECOND, older divergence still live at this commit: `v0.0.4` is published by the mirror
and absent from the dev repository. Nobody has ever noticed it, because until this file nothing
ever asked. The row's example was one instance of a class (§7j).

WHY STAGE 07 DID NOT ALREADY CLOSE THIS, stated because it very nearly did. `ensure_tag` now
creates / converges / refuses for BOTH tags, in the repo and on its remote, so a run that REACHES
the tag step can no longer leave the two repositories in different states. It cannot observe its
own NON-EXECUTION. `v0.0.18` was not mis-tagged; the step never ran. A guard inside the step is
blind to precisely the failure that happened, which is why the check below runs in the PREFLIGHT
of the NEXT cut — unconditionally, whatever the previous run did or did not do.

    THE PROPERTY:  a tag published by either repository must be published by both.
    THE OBSERVER:  the next cut's preflight, which does not care whether the last one finished.

WHAT EACH DELIVERABLE IS GRADED BY, named so a reader can check the mapping rather than trust it:

    1  reds on divergence, NAMING which tag and which side   TestDivergenceIsNamed
    2  it runs somewhere real, and the arms exist            TestTheWiring
    3  three states, and the third is not green              TestThreeStates
    4  the tail is GRADED — absence is observable            TestTheAbsenceCase   ← LOAD-BEARING
       the exit codes are the three states                   TestTheExitCodes
       no credential value is ever rendered                  TestSecretSafety
       the mirror boundary is unmoved                        TestTheBoundary

⛔ TAG NAMES ARE COMPARED, NEVER TAG COMMITS, and this is a correctness requirement rather than a
shortcut. The mirror is built by `sync-public.sh` as an independent squashed history, so `v0.0.17`
names a DIFFERENT commit in each repository BY DESIGN. A commit-wise comparison would report every
tag as divergent and would be switched off within one cut — which is how a check becomes a habit.

⚠ WHAT THIS DOES NOT GRADE, declared rather than discovered (doc 85 §7i):

  * **It does not watch between cuts.** `JasGujral/mokata` has Actions billing off
    (`tests/_release_repo_guards.py`, ① ), and the mirror's CI cannot read a private repository's
    refs, so there is no runner that can see BOTH sides except a developer machine. The observer is
    therefore `release.sh`'s preflight and nothing else. A divergence created after a cut is caught
    at the NEXT cut, not the moment it appears. That is a real latency and it is the whole of what
    was available; it is still strictly better than "a plan went looking".
  * **It does not run GitHub Actions.** The wiring is graded as `release.sh`'s CALL SITES, derived
    by `_release_retry`'s locator, never by searching for a spelling — the mistake that reddened
    four correct pins at stage 07 and the one that made a commented-out CI wait read as a live gate.

Requires git; the CLI legs are skipped without it. Real bare repositories in temp dirs, never a
double: a fake that answers "no tags" is indistinguishable from a repository that has none, which
is this row's own defect wearing a test's clothes (doc 85 §7e). Nothing outside temp dirs is
touched and the real mokata-oss checkout is never involved.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _support  # noqa: F401  (puts src/ on the path)
from _release_retry import case_arms, live_calls_in, push_invocations, tag_step_lines

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_tag_sets as C  # noqa: E402

#: Internal-only, so every class that reads it is guarded on it (row B1's shape, stage 05).
RELEASE_SH = os.path.join(ROOT, "scripts", "release.sh")
SYNC_SH = os.path.join(ROOT, "scripts", "sync-public.sh")
#: SHIPPED, and it must stay that way — this file imports it. `scripts/floor-python.sh`'s rule.
CHECK_RELPATH = "scripts/check_tag_sets.py"

_NO_GIT = shutil.which("git") is None
_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def reading(label, tags, error=""):
    """A readable (or, with `tags=None`, an UNREADABLE) side, without touching git."""
    return C.TagSetResult(label=label, spec="spec://" + label, tags=tags, error=error)


# ---- 1 · the divergence is NAMED, not merely counted -----------------------------------------

class TestDivergenceIsNamed(unittest.TestCase):
    """Deliverable 1. A count says something is wrong; a name says what to do about it."""

    def test_a_tag_set_divergence_reds(self):
        report = C.compare(reading("dev", ("v1",)), reading("mirror", ("v1", "v2")))
        self.assertEqual(report.state, C.DIVERGE)
        self.assertFalse(report.passing)

    def test_it_names_the_tag_missing_from_dev(self):
        report = C.compare(reading("dev", ("v1",)), reading("mirror", ("v1", "v2")))
        self.assertEqual(report.missing_from_dev, ("v2",))
        self.assertEqual(report.missing_from_mirror, ())

    def test_it_names_the_tag_missing_from_the_mirror(self):
        report = C.compare(reading("dev", ("v1", "v9")), reading("mirror", ("v1",)))
        self.assertEqual(report.missing_from_mirror, ("v9",))
        self.assertEqual(report.missing_from_dev, ())

    def test_a_divergence_in_the_MIRROR_direction_also_reds(self):
        """⚠ THE HALF THAT SURVIVED. Naming the tag and DECIDING the state are two facts, and a
        comparison that consults only `missing_from_dev` fills the tuple correctly and still
        answers AGREE — half this row, passing. Asserted as its own test because folding it into
        the naming test above lets either half cover for the other."""
        report = C.compare(reading("dev", ("v1", "v9")), reading("mirror", ("v1",)))
        self.assertEqual(report.state, C.DIVERGE)
        self.assertFalse(report.passing)
        self.assertNotEqual(report.exit_code, C.EXIT_AGREE)

    def test_both_directions_are_reported_from_one_reading(self):
        """Reporting only the first side found would hide half a two-sided divergence."""
        report = C.compare(reading("dev", ("v1", "v9")), reading("mirror", ("v1", "v2")))
        self.assertEqual((report.missing_from_dev, report.missing_from_mirror),
                         (("v2",), ("v9",)))

    def test_the_render_names_the_tag_and_the_side_it_is_missing_from(self):
        rendered = C.compare(reading("dev", ()), reading("mirror", ("v0.0.18",))).render()
        self.assertIn("v0.0.18", rendered)
        self.assertIn("dev", rendered)

    def test_identical_sets_agree(self):
        report = C.compare(reading("dev", ("v1", "v2")), reading("mirror", ("v2", "v1")))
        self.assertEqual(report.state, C.AGREE)
        self.assertTrue(report.passing)


# ---- 3 · three states, and the third is neither green nor red ---------------------------------

class TestThreeStates(unittest.TestCase):
    """Deliverable 3. Stage 06's vocabulary, because a fourth spelling of the same idea is §7f."""

    def test_the_three_states_are_three_distinct_values(self):
        self.assertEqual(len({C.AGREE, C.DIVERGE, C.NOT_CHECKABLE}), 3)

    def test_not_checkable_is_not_in_the_passing_set(self):
        self.assertNotIn(C.NOT_CHECKABLE, C.PASSING)
        self.assertEqual(C.PASSING, (C.AGREE,))

    def test_an_unreadable_side_is_not_checkable(self):
        report = C.compare(reading("dev", ("v1",)), reading("mirror", None, "no route to host"))
        self.assertEqual(report.state, C.NOT_CHECKABLE)
        self.assertFalse(report.passing)

    def test_not_checkable_is_decided_before_the_comparison(self):
        """⛔ ORDER IS LOAD-BEARING. An unreadable side beside an EMPTY readable one is the exact
        input on which "compare first, then notice" answers AGREE — every tag looks missing from
        nobody. `disclosure.resolve`'s mutant, one file over."""
        report = C.compare(reading("dev", ()), reading("mirror", None, "no route to host"))
        self.assertEqual(report.state, C.NOT_CHECKABLE)

    def test_not_checkable_names_which_side_could_not_be_read_and_why(self):
        report = C.compare(reading("dev", ("v1",)), reading("mirror", None, "no route to host"))
        self.assertEqual(report.unreadable, ("mirror",))
        self.assertIn("no route to host", report.render())

    def test_both_sides_unreadable_names_both(self):
        report = C.compare(reading("dev", None, "a"), reading("mirror", None, "b"))
        self.assertEqual(report.unreadable, ("dev", "mirror"))

    def test_a_repository_with_no_tags_is_READ_not_UNKNOWN(self):
        """§7g one level down: `()` is an answer, `None` is the absence of one. Collapsing them
        makes an offline run and a brand-new repository the same fact."""
        report = C.compare(reading("dev", ()), reading("mirror", ()))
        self.assertEqual(report.state, C.AGREE)
        self.assertTrue(reading("dev", ()).readable)
        self.assertFalse(reading("dev", None, "x").readable)


# ---- 4 · THE LOAD-BEARING ONE: the tail is graded, so an ABSENCE is observable ------------------

class TestTheAbsenceCase(unittest.TestCase):
    """Deliverable 4. `v0.0.18` was not mis-tagged. The step never ran, and that is what must be
    visible — a check that fires only when the step fires would have graded nothing."""

    DEV_AT_0_0_17 = ("v0.0.15", "v0.0.16", "v0.0.17")
    MIRROR_AT_0_0_18 = ("v0.0.15", "v0.0.16", "v0.0.17", "v0.0.18")

    def test_the_tag_step_never_ran_is_a_divergence_naming_the_absent_tag(self):
        report = C.compare(reading("dev", self.DEV_AT_0_0_17),
                           reading("mirror", self.MIRROR_AT_0_0_18))
        self.assertEqual(report.state, C.DIVERGE)
        self.assertEqual(report.missing_from_dev, ("v0.0.18",))

    def test_the_tag_step_ran_and_agreed_is_agreement(self):
        report = C.compare(reading("dev", self.MIRROR_AT_0_0_18),
                           reading("mirror", self.MIRROR_AT_0_0_18))
        self.assertEqual(report.state, C.AGREE)

    def test_skipped_and_agreed_are_distinguishable(self):
        """The pair, asserted as a pair. Either one alone passes while the other regresses."""
        skipped = C.compare(reading("dev", self.DEV_AT_0_0_17),
                            reading("mirror", self.MIRROR_AT_0_0_18))
        agreed = C.compare(reading("dev", self.MIRROR_AT_0_0_18),
                           reading("mirror", self.MIRROR_AT_0_0_18))
        self.assertNotEqual(skipped.state, agreed.state)
        self.assertNotEqual(skipped.render(), agreed.render())

    def test_a_tag_absent_from_BOTH_is_agreement(self):
        """The preflight of a cut in progress. The version being released is owed by nobody yet,
        and reding here would make the check impossible to keep switched on."""
        report = C.compare(reading("dev", self.DEV_AT_0_0_17),
                           reading("mirror", self.DEV_AT_0_0_17))
        self.assertEqual(report.state, C.AGREE)


# ---- the exit codes ARE the three states, proven against real repositories ----------------------

@unittest.skipUnless(not _NO_GIT, "git is required")
class TestTheExitCodes(unittest.TestCase):
    """Three states, three exit codes, run as a process — the form `release.sh` consumes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mokata-b3-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def git(self, *args, cwd=None):
        env = dict(os.environ)
        env.update(_GIT_ENV)
        return subprocess.run(("git",) + args, cwd=cwd or self.tmp, env=env,
                              capture_output=True, text=True, check=True)

    def repo(self, name, tags):
        """A REAL bare repository publishing exactly `tags`. Bare, because a bare repo's refs ARE
        what it publishes — the question this check asks."""
        work = os.path.join(self.tmp, name + "-work")
        bare = os.path.join(self.tmp, name + ".git")
        self.git("init", "-q", "-b", "main", work)
        with open(os.path.join(work, "f"), "w", encoding="utf-8") as fh:
            fh.write(name)
        self.git("add", "-A", cwd=work)
        self.git("commit", "-qm", "c", cwd=work)
        for tag in tags:
            self.git("tag", "-a", tag, "-m", tag, cwd=work)
        self.git("clone", "-q", "--bare", work, bare)
        return bare

    def run_check(self, dev, mirror):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "check_tag_sets.py"),
             "--dev", dev, "--mirror", mirror],
            capture_output=True, text=True)

    def test_agreement_exits_zero(self):
        dev = self.repo("dev", ("v1", "v2"))
        mirror = self.repo("mirror", ("v2", "v1"))
        done = self.run_check(dev, mirror)
        self.assertEqual(done.returncode, C.EXIT_AGREE, done.stdout + done.stderr)

    def test_a_real_divergence_exits_one_and_prints_the_missing_tag(self):
        dev = self.repo("dev", ("v1",))
        mirror = self.repo("mirror", ("v1", "v2"))
        done = self.run_check(dev, mirror)
        self.assertEqual(done.returncode, C.EXIT_DIVERGE, done.stdout + done.stderr)
        self.assertIn("v2", done.stdout + done.stderr)

    def test_an_unreachable_remote_exits_two_and_is_NOT_zero(self):
        dev = self.repo("dev", ("v1",))
        done = self.run_check(dev, os.path.join(self.tmp, "there-is-no-repository-here.git"))
        self.assertEqual(done.returncode, C.EXIT_NOT_CHECKABLE, done.stdout + done.stderr)
        self.assertNotEqual(done.returncode, C.EXIT_AGREE)

    def test_the_three_exit_codes_are_three_numbers(self):
        self.assertEqual(len({C.EXIT_AGREE, C.EXIT_DIVERGE, C.EXIT_NOT_CHECKABLE}), 3)


class TestTheLsRemoteReading(unittest.TestCase):
    """`git ls-remote --tags` prints the tag OBJECT and the peeled commit. Both name ONE tag."""

    TEXT = ("1b49f04004319faa80f02c9a50d6dbeadccb07d9\trefs/tags/v1\n"
            "6f12f60e7521e0d3adfda6fa8a47dd29ee6130aa\trefs/tags/v1^{}\n"
            "f0d03cdb2e621dd77761b6849ef75698ae5eb8bd\trefs/tags/v2\n")

    def test_the_peeled_line_is_not_a_second_tag(self):
        self.assertEqual(C.parse_ls_remote(self.TEXT), ("v1", "v2"))

    def test_no_tags_is_an_empty_reading_not_a_failure(self):
        self.assertEqual(C.parse_ls_remote(""), ())

    def test_a_ref_that_is_not_a_tag_is_not_read_as_one(self):
        """The reader is anchored on `refs/tags/`, not on "a line with a ref in it" — `ls-remote`
        without `--tags` prints heads too, and a caller that drops the flag must not silently
        start comparing branch names."""
        self.assertEqual(
            C.parse_ls_remote("f0d03cdb2e621dd77761b6849ef75698ae5eb8bd\trefs/heads/main\n"), ())


# ---- no credential value, anywhere -------------------------------------------------------------

class TestSecretSafety(unittest.TestCase):
    """This path holds a token and two remotes. A remote URL is the place a token gets carried,
    and this check's whole job is to PRINT the remotes it consulted."""

    TOKENED = "https://x-access-token:ghs_NOTAREALSECRET@github.com/JasGujral/mokata-oss.git"

    def test_userinfo_is_stripped_from_a_rendered_remote(self):
        self.assertNotIn("ghs_NOTAREALSECRET", C.redact_remote(self.TOKENED))
        self.assertIn("github.com/JasGujral/mokata-oss.git", C.redact_remote(self.TOKENED))

    def test_the_redaction_says_that_it_redacted(self):
        """A silently stripped URL and a URL that never had userinfo must not look identical —
        §7g, applied to the thing that is deliberately not shown."""
        self.assertNotEqual(C.redact_remote(self.TOKENED),
                            C.redact_remote("https://github.com/JasGujral/mokata-oss.git"))

    def test_a_reading_renders_the_redacted_spec_only(self):
        result = C.TagSetResult(label="mirror", spec=self.TOKENED, tags=None, error="boom")
        self.assertNotIn("ghs_NOTAREALSECRET", result.render())
        self.assertNotIn("ghs_NOTAREALSECRET",
                         C.compare(reading("dev", ()), result).render())


# ---- 2 · it runs somewhere REAL, and every state has an arm ------------------------------------

@unittest.skipUnless(os.path.exists(RELEASE_SH), "release.sh is internal-only; absent on the mirror")
class TestTheWiring(unittest.TestCase):
    """Deliverable 2. ⚠ Located at the CALL SITE by `_release_retry`'s derived locator, never by
    searching for a spelling: at stage 07 a spelling-search reddened four correct pins, and the
    same misreading once made a commented-out CI wait read as a live release gate."""

    @classmethod
    def setUpClass(cls):
        with open(RELEASE_SH, encoding="utf-8") as fh:
            cls.sh = fh.read()
        cls.calls = [n for n, _line in live_calls_in(cls.sh, "verify_tag_sets")]

    def test_the_check_is_called_by_the_release_script(self):
        self.assertTrue(self.calls, "release.sh never calls verify_tag_sets")

    def test_the_preflight_call_precedes_the_first_push(self):
        """The observer of an ABSENCE. It must not be reachable only via the tag step."""
        push = min(n for n, args in push_invocations(self.sh)
                   if "origin master" in args)
        self.assertTrue(any(n < push for n in self.calls),
                        "no verify_tag_sets call runs before the first push — nothing observes "
                        "a divergence left behind by a PREVIOUS cut")

    def test_the_tail_is_graded_after_the_tag_step(self):
        tag_at = tag_step_lines(self.sh)
        self.assertTrue(tag_at, "release.sh no longer tags")
        self.assertTrue(any(n > max(tag_at) for n in self.calls),
                        "nothing re-checks the tag sets after the tag step — the tail is present "
                        "but ungraded")

    def test_the_check_is_invoked_by_path_not_reimplemented_in_bash(self):
        self.assertIn(CHECK_RELPATH, self.sh)

    def test_every_exit_code_has_its_own_arm_and_the_unknown_one_refuses(self):
        """`verify_branch_protection`'s shape: three states plus a fourth arm for a code that is
        none of them. An unexpected exit code proves nothing and must never fall through.

        ⚠ AND AN ARM IS GRADED BY WHETHER IT EXITS, not by whether it exists. `case_arms` returns
        that second fact for exactly this reason one file over: a guard can have four arms and
        still be decorative if the refusing ones only print. A divergence arm that prints the
        whole remedy and then lets the cut continue is this row with a nicer error message."""
        arms = case_arms(_function_source(self.sh, "verify_tag_sets"), "rc")
        self.assertEqual(set(arms), {"0", "1", "2", "*"},
                         "verify_tag_sets' arms are %s" % sorted(arms))
        self.assertFalse(arms["0"], "the AGREE arm exits — the cut can never proceed")
        for code in ("1", "2", "*"):
            self.assertTrue(arms[code],
                            "the %s arm does not exit: it reports and the cut continues" % code)


def _function_source(text, name):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(name + "() {"):
            for end in range(index + 1, len(lines)):
                if lines[end] == "}":
                    return "\n".join(lines[index:end + 1])
    return ""


# ---- the mirror boundary is where it was ---------------------------------------------------------

@unittest.skipUnless(os.path.exists(SYNC_SH), "sync-public.sh is internal-only; absent on the mirror")
class TestTheBoundary(unittest.TestCase):
    """Both directions. An entry the controls exclude and this file needs is a broken shipped test;
    an entry that ships and should not is the leak. `scripts/floor-python.sh`'s rule, applied to
    the file THIS test imports."""

    @classmethod
    def setUpClass(cls):
        import _mirror_bookkeeping as MB
        with open(SYNC_SH, encoding="utf-8") as fh:
            script = fh.read()
        cls.excludes = MB.exclude_entries(script)
        cls.guarded = MB.guard_entries(script)

    def test_the_check_is_excluded_by_neither_control(self):
        """A shipped test imports it. Excluding it makes that test read a file the mirror does not
        have — `SHIPPED-TEST-READS-INTERNAL-FILE` arriving from the other direction."""
        self.assertNotIn(CHECK_RELPATH, self.excludes)
        self.assertIsNotNone(self.guarded, "INTERNAL_PATHS could not be located")
        self.assertNotIn(CHECK_RELPATH, self.guarded)

    def test_release_sh_is_still_excluded_by_both(self):
        self.assertIn("scripts/release.sh", self.excludes)
        self.assertIn("scripts/release.sh", self.guarded)

    def test_this_stage_added_nothing_to_either_control(self):
        """The brief's assert-unchanged, as an assertion rather than a diff read once by hand."""
        self.assertEqual(
            self.excludes & {CHECK_RELPATH, "tests/test_b3_release_tail_ungraded.py"}, frozenset())
        self.assertEqual(
            self.guarded & {CHECK_RELPATH, "tests/test_b3_release_tail_ungraded.py"}, frozenset())


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
